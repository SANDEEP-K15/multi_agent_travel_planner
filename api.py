"""HTTP API around the travel-planner graph.

Run locally:  uvicorn api:api --reload --port 8000

The web interface in ./web talks to these endpoints. Endpoints are plain
`def` (not `async def`) on purpose: FastAPI runs them in a worker thread, and
the agents call `asyncio.run(...)` for their MCP tools, which needs a thread
without a running event loop.
"""

import os
import sys
import uuid
from typing import Any

# The agents print debug output. On consoles that are not UTF-8 (Windows),
# printing a character such as the rupee sign would crash the request, so
# replace anything the console cannot show instead of raising.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from config import DATABASE_URL, get_llm
from graph import app as graph_app
from guardrails_service import check_user_input

api = FastAPI(title="Multi-Agent Travel Planner API")

_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

api.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

RESULT_FIELDS = (
    "supervisor_reasoning",
    "selected_agents",
    "flight_results",
    "hotel_results",
    "weather_results",
    "budget_results",
)


class PlanRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    user_id: str = Field(default="guest", max_length=60)


class ApprovalRequest(BaseModel):
    thread_id: str
    approved: bool
    feedback: str = Field(default="", max_length=2000)


class FollowUpRequest(BaseModel):
    thread_id: str
    question: str = Field(min_length=2, max_length=2000)


def _config(thread_id: str, user_id: str = "") -> dict[str, Any]:
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": "travel-planner-request",
        "tags": ["web", "travel-planner"],
        "metadata": {"thread_id": thread_id, "user_id": user_id},
    }


def _payload(thread_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Turn a graph result into the JSON shape the web interface expects."""
    body: dict[str, Any] = {"thread_id": thread_id}
    for field in RESULT_FIELDS:
        body[field] = result.get(field) or ([] if field == "selected_agents" else "")

    if result.get("guardrail_allowed") is False:
        body["status"] = "blocked"
        body["message"] = result.get("guardrail_message") or result.get("final_response", "")
        return body

    interrupts = result.get("__interrupt__")
    if interrupts:
        body["status"] = "awaiting_approval"
        body["draft_itinerary"] = interrupts[0].value.get("draft_itinerary", "")
        return body

    body["status"] = "done"
    body["final_response"] = result.get("final_response", "")
    return body


def _run(payload: Any, config: dict[str, Any]) -> dict[str, Any]:
    try:
        return graph_app.invoke(payload, config=config)
    except Exception as exc:  # surfaced to the interface as a readable error
        raise HTTPException(
            status_code=502,
            detail=f"The planning agents failed: {type(exc).__name__}: {exc}",
        ) from exc


@api.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ready",
        "features": {
            "guardrails": True,
            "mcp": True,
            "human_approval": True,
            "langsmith": bool(os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")),
            "checkpointer": "postgres" if DATABASE_URL else "memory",
        },
    }


@api.post("/plan")
def plan(request: PlanRequest) -> dict[str, Any]:
    """Run the agents until the draft itinerary is ready for approval."""
    thread_id = f"{request.user_id}_{uuid.uuid4().hex[:8]}"
    result = _run(
        {
            "messages": [HumanMessage(content=request.query)],
            "user_id": request.user_id,
            "user_query": request.query,
            "flight_results": "",
            "hotel_results": "",
            "weather_results": "",
            "budget_results": "",
            "itinerary": "",
            "final_response": "",
            "llm_calls": 0,
        },
        _config(thread_id, request.user_id),
    )
    return _payload(thread_id, result)


@api.post("/approve")
def approve(request: ApprovalRequest) -> dict[str, Any]:
    """Resume a paused thread with the human's approval or feedback."""
    config = _config(request.thread_id)
    snapshot = graph_app.get_state(config)
    if not snapshot.next:
        raise HTTPException(
            status_code=409,
            detail="This plan is no longer waiting for approval. Start a new trip.",
        )

    # Normally the thread is paused at the approval interrupt and we resume it
    # with the human's answer. If an earlier attempt already recorded the answer
    # and then failed (for example on a model rate limit), there is no interrupt
    # left, so continue the thread from where it stopped instead.
    waiting_for_human = any(task.interrupts for task in snapshot.tasks)
    payload = (
        Command(resume={"approved": request.approved, "feedback": request.feedback})
        if waiting_for_human
        else None
    )
    result = _run(payload, config)
    return _payload(request.thread_id, result)


@api.post("/followup")
def followup(request: FollowUpRequest) -> dict[str, Any]:
    """Answer a question about a saved plan with one model call, no agents."""
    snapshot = graph_app.get_state(_config(request.thread_id))
    plan_text = snapshot.values.get("final_response") or snapshot.values.get("itinerary")
    if not plan_text:
        raise HTTPException(
            status_code=404,
            detail="No saved plan was found for this conversation. Start a new trip.",
        )

    allowed, message = check_user_input(request.question)
    if not allowed:
        return {"status": "blocked", "answer": message}

    try:
        response = get_llm().invoke(
            [
                SystemMessage(
                    content=(
                        "You answer follow-up questions about a travel plan that was "
                        "already produced. Use only the plan and notes provided. If the "
                        "answer is not in them, say so and suggest planning a new trip. "
                        "Prices are estimates, never guaranteed live prices."
                    )
                ),
                HumanMessage(
                    content=(
                        f"Original request:\n{snapshot.values.get('user_query', '')}\n\n"
                        f"Travel plan:\n{plan_text}\n\n"
                        f"Budget notes:\n{snapshot.values.get('budget_results', '')}\n\n"
                        f"Follow-up question:\n{request.question}"
                    )
                ),
            ]
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"The model call failed: {type(exc).__name__}: {exc}",
        ) from exc

    return {"status": "done", "answer": response.content}

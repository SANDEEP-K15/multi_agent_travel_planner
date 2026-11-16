import asyncio
import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from config import get_llm
from mcp_client import (
    weather_mcp_search as current_weather,
    forecast_mcp_search as forecast,
    get_airlines as list_airlines,
    get_airports as list_airports,
    tavily_mcp_search as tavily_search,
)

from guardrails_service import check_user_input, filter_final_output

from state import TravelState

llm = get_llm()

def _llm_text(system: str, prompt: str) -> str:
    response = llm.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=prompt),
        ]
    )
    return response.content

ALL_AGENTS = [
    "flight_agent",
    "hotel_agent",
    "weather_agent",
    "budget_agent",
    "itinerary_agent",
]

EMPTY_CONSTRAINTS = {
    "destination": "",
    "origin": "",
    "duration": "",
    "budget": "",
    "travel_style": "",
    "special_preferences": [],
}


def _run_tool(coroutine, label: str):
    """Run an MCP tool call; a failed tool must not fail the whole plan."""
    try:
        return asyncio.run(coroutine)
    except Exception as exc:
        print(f"[{label}] tool call failed: {type(exc).__name__}: {exc}")
        return f"{label} data is unavailable right now."


def _clip(text: Any, limit: int) -> str:
    """Cap how much of one agent's output is passed into another prompt.

    Groq's free tier limits tokens per minute, and a single prompt that carries
    every agent's full output can exceed that limit on its own.
    """
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[shortened]"


def _format_search_results(result: Any, max_results: int = 5) -> str:
    """Turn the Tavily MCP response into a short, readable list."""
    lines: list[str] = []
    blocks = result if isinstance(result, list) else [result]
    for block in blocks:
        text = block.get("text") if isinstance(block, dict) else None
        if not text:
            continue
        try:
            data = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for item in (data.get("results") or [])[:max_results]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Result").strip()
            url = str(item.get("url") or "").strip()
            snippet = " ".join(str(item.get("content") or "").split())[:320]
            heading = f"[{title}]({url})" if url else title
            lines.append(f"- **{heading}**: {snippet}")
    if lines:
        return "\n".join(lines)
    return _clip(result, 2500)


def _json_from_llm(text: str) -> dict:
    print("\n========== RAW LLM RESPONSE ==========")
    print(text)
    print("======================================\n")

    start = text.index("{")
    end = text.rindex("}") + 1

    json_text = text[start:end]

    print("\n========== EXTRACTED JSON ==========")
    print(json_text)
    print("====================================\n")

    return json.loads(json_text)


def input_guardrail_agent(state: TravelState):
    allowed, message = check_user_input(state["user_query"])

    if not allowed:
        return {
            "guardrail_allowed": False,
            "guardrail_message": message,
            "selected_agents": [],
            "final_response": message,
            "messages": [AIMessage(content=message)],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    return {
        "guardrail_allowed": True,
        "guardrail_message": "",
        "messages": [AIMessage(content="Input guardrail passed.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


# with guardrail
def supervisor_agent(state: TravelState):
    query = state["user_query"]
    
    prompt = f"""
You are the supervisor of a real-world multi-agent travel planning system.

Decide which specialist agents are needed for this user request.

Available agents:
- flight_agent: use when flights, airports, airlines, routes, or airfare guidance are needed
- hotel_agent: use when hotels, stays, neighborhoods, or accommodation are needed
- weather_agent: use when weather, climate, season, packing, or forecast is useful
- budget_agent: use when budget, affordability, cost, or price constraints are mentioned
- itinerary_agent: almost always needed to produce the travel plan

Return only JSON with this schema:
{{
  "selected_agents": ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"],
  "trip_constraints": {{
    "destination": "",
    "origin": "",
    "duration": "",
    "budget": "",
    "travel_style": "",
    "special_preferences": []
  }},
  "reasoning": ""
}}

User request:
{query}
"""

 
    raw = _llm_text(
        "You route work to specialist agents. Return strict JSON only.",
        prompt,
    )

    print("\n========== RAW LLM RESPONSE ==========")
    print(raw)
    print("======================================\n")

    # The model sometimes omits a field or returns invalid JSON. Fall back to
    # running every agent rather than failing the request.
    try:
        parsed = _json_from_llm(raw)
    except (ValueError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    
    print("\n========== PARSED JSON ==========")
    print(json.dumps(parsed, indent=2))
    print("=================================\n")
    
    '''
    At first glance they look the same, but they're not:

    raw → string returned by the LLM
    parsed → Python dictionary created from that string

    If you really want to demonstrate the difference, add:

    print(type(raw))
    print(type(parsed))
    Output:

    <class 'str'>
    <class 'dict'>
    Yes, you can get output from a string, but it's much harder and less reliable.
    '''
    
    selected = parsed.get("selected_agents")
    if not isinstance(selected, list):
        selected = []
    selected = [agent for agent in selected if agent in ALL_AGENTS] or list(ALL_AGENTS)

    constraints = parsed.get("trip_constraints")
    if not isinstance(constraints, dict):
        constraints = {}
    constraints = {**EMPTY_CONSTRAINTS, **constraints}

    return {
        "selected_agents": selected,
        "trip_constraints": constraints,
        "supervisor_reasoning": str(parsed.get("reasoning") or ""),
        "messages": [AIMessage(content="Supervisor created the agent plan.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }






# without guardrail

# def supervisor_agent(state: TravelState):
#     query = state["user_query"]
#     prompt = f"""
# You are the supervisor of a real-world multi-agent travel planning system.

# Decide which specialist agents are needed for this user request.

# Available agents:
# - flight_agent: use when flights, airports, airlines, routes, or airfare guidance are needed
# - hotel_agent: use when hotels, stays, neighborhoods, or accommodation are needed
# - weather_agent: use when weather, climate, season, packing, or forecast is useful
# - budget_agent: use when budget, affordability, cost, or price constraints are mentioned
# - itinerary_agent: almost always needed to produce the travel plan

# Return only JSON with this schema:
# {{
#   "selected_agents": ["flight_agent", "hotel_agent", "weather_agent", "budget_agent", "itinerary_agent"],
#   "trip_constraints": {{
#     "destination": "",
#     "origin": "",
#     "duration": "",
#     "budget": "",
#     "travel_style": "",
#     "special_preferences": []
#   }},
#   "reasoning": ""
# }}

# User request:
# {query}
# """
    
#     raw = _llm_text(
#         "You route work to specialist agents. Return strict JSON only.",
#         prompt,
#     )

#     print("\n========== RAW LLM RESPONSE ==========")
#     print(raw)
#     print("======================================\n")

#     parsed = _json_from_llm(raw)
#     print("\n========== PARSED JSON ==========")
#     print(json.dumps(parsed, indent=2))
#     print("=================================\n")

#     print(type(raw))
#     print(type(parsed))

#     selected = parsed["selected_agents"]       

#     return {
#         "selected_agents": selected,
#         "trip_constraints": parsed["trip_constraints"],
#         "supervisor_reasoning": parsed["reasoning"],
#         "messages": [AIMessage(content="Supervisor created the agent plan.")],
#         "llm_calls": state.get("llm_calls", 0) + 1,
#     }



def flight_agent(state: TravelState):
    query = state["user_query"]
    constraints = state.get("trip_constraints") or {}
    destination = constraints.get("destination", "")

    print("\n========== FLIGHT AGENT INPUT ==========")
    print("Query:", query)
    print("Constraints:", constraints)
    print("========================================\n")

    airports = _run_tool(list_airports(), "Airport")
    airlines = _run_tool(list_airlines(), "Airline")

    print("\n========== AIRPORT MCP DATA ==========")
    print(airports)
    print("======================================\n")

    print("\n========== AIRLINE MCP DATA ==========")
    print(airlines)
    print("======================================\n")

    prompt = f"""
Create flight guidance for this trip.

User request:
{query}

Trip constraints:
{constraints}

Airport MCP data:
{str(airports)[:3000]}

Airline MCP data:
{str(airlines)[:3000]}

Include likely departure/arrival airports, relevant airlines,
estimated duration, fare range, peak season warning,
and booking advice.
"""

    result = _llm_text(
        "You are a flight planning specialist.",
        prompt,
    )

    print("\n========== FLIGHT AGENT OUTPUT ==========")
    print(result)
    print("=========================================\n")

    return {
        "flight_results": result,
        "messages": [AIMessage(content="Flight agent completed.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }




def hotel_agent(state: TravelState):
    query = f"Best hotels and areas to stay for: {state['user_query']}"

    print("\n========== HOTEL AGENT INPUT ==========")
    print(query)
    print("=======================================\n")

    result = _run_tool(tavily_search(query), "Hotel search")

    print("\n========== HOTEL SEARCH RESULT ==========")
    print(result)
    print("=========================================\n")

    return {
        "hotel_results": _format_search_results(result),
        "messages": [AIMessage(content="Hotel agent completed.")],
    }


def weather_agent(state: TravelState):
    constraints = state.get("trip_constraints") or {}
    city = constraints.get("destination", "")

    print("\n========== WEATHER AGENT INPUT ==========")
    print("City:", city)
    print("=========================================\n")

    weather_data = _run_tool(current_weather(city), "Current weather")
    forecast_data = _run_tool(forecast(city), "Weather forecast")

    print("\n========== CURRENT WEATHER ==========")
    print(weather_data)
    print("=====================================\n")

    print("\n========== WEATHER FORECAST ==========")
    print(forecast_data)
    print("======================================\n")

    result = f"""
Current weather:
{weather_data}

Forecast:
{forecast_data}
"""

    print("\n========== WEATHER AGENT OUTPUT ==========")
    print(result)
    print("==========================================\n")

    return {
        "weather_results": result,
        "messages": [AIMessage(content="Weather agent completed.")],
    }



def budget_agent(state: TravelState):

    print("\n========== BUDGET AGENT INPUT ==========")
    print("Trip Constraints:")
    print(state.get("trip_constraints"))
    print("\nFlight Results:")
    print(state.get("flight_results"))
    print("\nHotel Results:")
    print(state.get("hotel_results"))
    print("\nWeather Results:")
    print(state.get("weather_results"))
    print("=========================================\n")

    prompt = f"""
Analyze whether this trip plan is realistic for the user's budget.

User request:
{state['user_query']}

Constraints:
{state.get('trip_constraints', {})}

Flight results:
{_clip(state.get('flight_results', ''), 2500)}

Hotel results:
{_clip(state.get('hotel_results', ''), 2500)}

Weather results:
{_clip(state.get('weather_results', ''), 1500)}

Return a concise budget assessment with:
1. estimated cost categories
2. risk areas
3. money-saving suggestions
4. whether the plan seems feasible
"""

    result = _llm_text(
        "You are a practical travel budget analyst.",
        prompt,
    )

    print("\n========== BUDGET AGENT OUTPUT ==========")
    print(result)
    print("=========================================\n")

    return {
        "budget_results": result,
        "messages": [AIMessage(content="Budget agent completed.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }



def itinerary_agent(state: TravelState):

    print("\n========== ITINERARY AGENT INPUT ==========")
    print("Trip Constraints:")
    print(state.get("trip_constraints"))

    print("\nFlight Results:")
    print(state.get("flight_results"))

    print("\nHotel Results:")
    print(state.get("hotel_results"))

    print("\nWeather Results:")
    print(state.get("weather_results"))

    print("\nBudget Results:")
    print(state.get("budget_results"))
    print("===========================================\n")

    prompt = f"""
Create a clear draft travel itinerary.

User request:
{state['user_query']}

Trip constraints:
{state.get('trip_constraints', {})}

Flight results:
{_clip(state.get('flight_results', ''), 2500)}

Hotel results:
{_clip(state.get('hotel_results', ''), 2500)}

Weather results:
{_clip(state.get('weather_results', ''), 1500)}

Budget results:
{_clip(state.get('budget_results', ''), 2500)}

Make the output structured, practical, and ready for human review.
"""

    result = _llm_text(
        "You are an expert itinerary planner.",
        prompt,
    )

    print("\n========== ITINERARY OUTPUT ==========")
    print(result)
    print("======================================\n")

    approval_request = f"""
Please review this draft travel plan.

{result}

Reply with approval or feedback.
"""

    return {
        "itinerary": result,
        "approval_request": approval_request,
        "messages": [AIMessage(content="Draft itinerary created for human review.")],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def human_approval_agent(state: TravelState):
    feedback = interrupt(
        {
            "question": "Do you approve this itinerary?",
            "draft_itinerary": state.get("itinerary", ""),
            "approval_request": state.get("approval_request", ""),
            "expected_response": {
                "approved": True,
                "feedback": "Optional feedback for revision",
            },
        }
    )

    approved = feedback["approved"]
    human_feedback = feedback["feedback"]

    return {
        "approved": approved,
        "human_feedback": human_feedback,
        "messages": [AIMessage(content="Human approval step completed.")],
    }



def final_response_agent(state: TravelState):

    print("\n========== FINAL AGENT INPUT ==========")
    print("Approved:", state.get("approved"))
    print("Feedback:", state.get("human_feedback"))
    print("=======================================\n")

    if state["approved"]:
        prompt = f"""
The human approved this draft itinerary.

Produce the final polished travel plan.

Draft itinerary:
{_clip(state.get('itinerary', ''), 6000)}

Budget notes:
{_clip(state.get('budget_results', ''), 2000)}
"""
    else:
        prompt = f"""
The human did not approve the draft.

Original user request:
{state['user_query']}

Draft itinerary:
{_clip(state.get('itinerary', ''), 6000)}

Human feedback:
{state['human_feedback']}

Budget notes:
{_clip(state.get('budget_results', ''), 2000)}
"""

    result = _llm_text(
        "You produce final user-ready travel plans.",
        prompt,
    )

    print("\n========== FINAL RESPONSE ==========")
    print(result)
    print("====================================\n")

    return {
        "final_response": result,
        "messages": [AIMessage(content=result)],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }


def output_guardrail_agent(state: TravelState):
    guarded_response = filter_final_output(
        user_input=state["user_query"],
        candidate=state.get("final_response", ""),
    )

    return {
        "final_response": guarded_response,
        "messages": [AIMessage(content=guarded_response)],
        "llm_calls": state.get("llm_calls", 0) + 1,
    }
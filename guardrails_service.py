import time
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableLambda
from nemoguardrails import RailsConfig
from nemoguardrails.integrations.langchain.runnable_rails import RunnableRails
from langsmith import traceable

BASE_DIR = Path(__file__).resolve().parent


INPUT_ALLOWED = "__NEMO_INPUT_ALLOWED__"

INPUT_BLOCKED_MESSAGE = (
    "I can only help with travel-planning requests such as flights, "
    "hotels, weather, budgets and itineraries."
)

OUTPUT_BLOCKED_MESSAGE = (
    "The generated travel plan could not be displayed because it did "
    "not pass the output safety check."
)


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value

    if hasattr(value, "content"):
        return str(value.content)

    if isinstance(value, dict):
        for key in ("output", "answer", "content"):
            if value.get(key) is not None:
                return str(value[key])

    return str(value)


# The lambda runs only when NeMo permits the input.
_input_config = RailsConfig.from_path(
    str(BASE_DIR / "guardrails" / "input")
)

_input_guard = RunnableRails(
    config=_input_config,
    runnable=RunnableLambda(lambda _: INPUT_ALLOWED),
    input_blocked_message=INPUT_BLOCKED_MESSAGE,
    input_key="input",
    output_key="output",
)


# This lambda represents your existing application output.
# NeMo checks that returned value using the output rail.
_output_config = RailsConfig.from_path(
    str(BASE_DIR / "guardrails" / "output")
)

_output_guard = RunnableRails(
    config=_output_config,
    runnable=RunnableLambda(lambda data: data["candidate"]),
    input_blocked_message=INPUT_BLOCKED_MESSAGE,
    output_blocked_message=OUTPUT_BLOCKED_MESSAGE,
    input_key="input",
    output_key="output",
)

# Seconds to wait before each retry when the model provider rate-limits a
# guardrail check. The checks share the provider's per-minute token budget
# with the agents, so a check can land just after that budget is used up.
RATE_LIMIT_WAITS = (8, 12, 20, 25, 30)


def _is_rate_limit(error: Exception) -> bool:
    text = str(error).lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def _invoke_with_retry(guard: RunnableRails, payload: Any) -> Any:
    """Run a guardrail check, waiting out provider rate limits."""
    for wait in RATE_LIMIT_WAITS:
        try:
            return guard.invoke(payload)
        except Exception as error:
            if not _is_rate_limit(error):
                raise
            print(f"Guardrail check was rate-limited; retrying in {wait}s")
            time.sleep(wait)
    return guard.invoke(payload)


@traceable(name="nemo-input-guardrail", run_type="chain")
def check_user_input(user_input: str) -> tuple[bool, str]:
    result = _invoke_with_retry(_input_guard, user_input)
    text = _extract_text(result)

    if text == INPUT_ALLOWED:
        return True, ""

    return False, text or INPUT_BLOCKED_MESSAGE

@traceable(name="nemo-output-guardrail", run_type="chain")
def filter_final_output(user_input: str, candidate: str) -> str:
    result = _invoke_with_retry(
        _output_guard,
        {
            "input": user_input,
            "candidate": candidate,
        },
    )

    return _extract_text(result)
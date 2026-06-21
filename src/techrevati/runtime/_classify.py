"""Pure exception-classification helpers, extracted from the orchestrator.

These map raw exceptions onto the recovery :class:`FailureScenario` and the
event-level :class:`AgentFailureClass` taxonomies. They are deliberately free of
any session or worker state, so this correctness-critical logic (it drives both
recovery and audit-event fidelity) can be unit-tested in isolation. The
orchestrator imports them back and keeps ``_failure_class_for_exception`` — the
exception-type dispatch — next to the exception classes it owns.
"""

from __future__ import annotations

from techrevati.runtime.agent_events import AgentFailureClass
from techrevati.runtime.retry_policy import FailureScenario

#: Substrings that mark an exception chain as a prompt-safety rejection.
_PROMPT_REJECTION_MARKERS = (
    "prompt rejected",
    "prompt rejection",
    "content policy",
    "content filter",
    "safety policy",
    "blocked by safety",
    "moderation",
    "jailbreak",
    "unsafe prompt",
    "disallowed content",
)


def _scenario_to_class(scenario: FailureScenario) -> AgentFailureClass:
    """Map a recovery FailureScenario to the event-level taxonomy."""
    mapping: dict[FailureScenario, AgentFailureClass] = {
        FailureScenario.LLM_TIMEOUT: AgentFailureClass.LLM_TIMEOUT,
        FailureScenario.LLM_ERROR: AgentFailureClass.LLM_ERROR,
        FailureScenario.TOOL_EXECUTION_ERROR: AgentFailureClass.TOOL_ERROR,
        FailureScenario.CONTEXT_OVERFLOW: AgentFailureClass.CONTEXT_OVERFLOW,
        FailureScenario.DEPENDENCY_TIMEOUT: AgentFailureClass.DEPENDENCY_FAILED,
        FailureScenario.MEMORY_CORRUPTION: AgentFailureClass.MEMORY_CORRUPTION,
        FailureScenario.PROVIDER_FAILURE: AgentFailureClass.DEPENDENCY_FAILED,
    }
    return mapping.get(scenario, AgentFailureClass.UNKNOWN)


def _is_prompt_rejection_exception(exc: Exception) -> bool:
    """Return True when an exception chain looks like a prompt safety rejection."""
    seen: set[int] = set()
    cursor: BaseException | None = exc
    while cursor is not None and id(cursor) not in seen:
        seen.add(id(cursor))
        message = str(cursor).lower()
        if any(marker in message for marker in _PROMPT_REJECTION_MARKERS):
            return True
        nxt: BaseException | None = cursor.__cause__
        if nxt is None and not cursor.__suppress_context__:
            nxt = cursor.__context__
        cursor = nxt
    return False


def _safe_exception_detail(exc: Exception) -> str:
    """Describe a terminal exception without copying its message into events."""
    return f"{type(exc).__name__} raised"

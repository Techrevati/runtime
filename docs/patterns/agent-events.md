# Agent Events

Typed lifecycle events with a failure taxonomy. JSON-serializable,
round-trippable, and convertible to telemetry attributes.

## Usage

```python
from techrevati.runtime import AgentEvent, AgentFailureClass

event = AgentEvent.failed(
    role="writer",
    phase="draft",
    failure_class=AgentFailureClass.LLM_TIMEOUT,
    detail="30s",
).with_project(42)

tracer.start_span("agent.execution", attributes=event.to_otel_attributes())
```

## Convenience constructors

- `AgentEvent.started(role, phase)`
- `AgentEvent.ready(role, phase, detail=None, data=None)`
- `AgentEvent.blocked(role, phase, detail=None, data=None)`
- `AgentEvent.completed(role, phase, detail=None)`
- `AgentEvent.failed(role, phase, failure_class, detail=None)`
- `AgentEvent.tool_called(role, phase, tool)`
- `AgentEvent.tool_completed(role, phase, tool)`
- `AgentEvent.phase_started(phase)`
- `AgentEvent.gate_passed(phase, detail=None)`
- `AgentEvent.gate_failed(phase, detail=None)`
- `AgentEvent.recovery_attempted(role, phase, detail=None)`
- `AgentEvent.recovery_succeeded(role, phase, detail=None, data=None)`
- `AgentEvent.recovery_failed(role, phase, detail=None, data=None)`
- `AgentEvent.recovery_escalated(role, phase, detail=None, data=None)`

`AgentSession` and `AsyncOrchestrationSession` automatically emit
`agent.started` on session entry and attach `project_id` to session
events when the session has one.
Permission and guardrail tool blocks emit `agent.blocked` with tool
metadata only; tool outputs and guardrail reasons are not copied into
event data.
If those blocks escape the session context, the terminal `agent.failed`
event uses `failure_class="permission_denied"` or
`failure_class="guardrail_violation"`. Governance hard-stops use
`failure_class="governance_breach"` on both the `governance.breach`
event and the terminal `agent.failed` event.
Model-call exceptions emit `agent.recovery.attempted` followed by an
outcome event (`agent.recovery.succeeded`, `agent.recovery.failed`, or
`agent.recovery.escalated`) with scenario and recovery-result metadata.
Terminal `ValueError` and `TypeError` fallbacks use
`failure_class="validation_error"` after the classifier has had a chance to
recognize more specific context, dependency, timeout, and provider failures.
This keeps bad caller input separate from model/provider outages without
masking specific failure classes.
Provider or model messages that indicate prompt/content-policy rejection use
`failure_class="prompt_rejection"` before that validation fallback, so safety
blocks stay visible in pilot failure-class distribution without copying prompt
text or provider error details into event payloads.
Runtime rate-limiter exceptions use `failure_class="rate_limit"` on the
terminal event, matching budget and usage-limit stop semantics.
Human-input pauses emit `agent.blocked` while waiting and `agent.ready`
after input arrives. Prompt and response text are not copied into event
data.

## Mutation through `with_*`

All events are `frozen` dataclasses. To enrich, use `replace`-style builders:

- `.with_failure_class(fc)`
- `.with_detail(detail)`
- `.with_data(dict)`
- `.with_project(project_id)`

## Serialization

| Method | Returns |
|---|---|
| `to_dict()` | Dict with both `event` (full path like `agent.failed`) and `type` (short tail like `failed`) |
| `to_json()` | JSON string |
| `to_otel_attributes()` | Dict of telemetry semantic-convention keys |
| `AgentEvent.from_dict(d)` | Reconstruct from a dict |
| `AgentEvent.from_json(s)` | Reconstruct from a JSON string |

## Enums

- `AgentEventName` — full path of every emit-able event
- `AgentEventStatus` — coarse status at emission time
- `AgentFailureClass` — failure taxonomy (`LLM_TIMEOUT`, `LLM_ERROR`, `TOOL_ERROR`, `CONTEXT_OVERFLOW`, `RATE_LIMIT`, `DEPENDENCY_FAILED`, `GOVERNANCE_BREACH`, `PERMISSION_DENIED`, `GUARDRAIL_VIOLATION`, `MEMORY_CORRUPTION`, `VALIDATION_ERROR`, `PROMPT_REJECTION`, `CANCELLED`, `UNKNOWN`)

Runtime safety caps are intentionally typed in that taxonomy. For example,
an uncaught `MaxIterationsExceededError` from `AgentSession.max_iterations`
uses `GOVERNANCE_BREACH`, not `LLM_ERROR`, in the terminal `agent.failed`
event.
Async session cancellation uses `CANCELLED`, not `UNKNOWN`, so audit consumers
can distinguish caller stop signals from unclassified failures.
Cancellation events also carry `AgentEventStatus.CANCELLED`; the OTel sink
exports `techrevati.failure_class="cancelled"` without setting `error.type` or
`StatusCode.ERROR`, so intentional stops do not inflate error-rate alerts.
The schema rejects inconsistent `agent.failed` cancellation payloads: a
`cancelled` failure class must use `status="cancelled"`, and
`status="cancelled"` must use `failure_class="cancelled"`.
Every `agent.failed` payload must include a valid `failure_class`; missing
failure classes are rejected during construction and `from_dict(...)`
reconstruction.

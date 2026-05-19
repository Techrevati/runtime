# Agent Events

Typed lifecycle events with a failure taxonomy. JSON-serializable, round-trippable, and convertible to OpenTelemetry attributes.

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
- `AgentEvent.completed(role, phase, detail=None)`
- `AgentEvent.failed(role, phase, failure_class, detail=None)`
- `AgentEvent.phase_started(phase)`
- `AgentEvent.gate_passed(phase, detail=None)`
- `AgentEvent.gate_failed(phase, detail=None)`
- `AgentEvent.recovery_attempted(role, phase, detail=None)`

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
| `to_otel_attributes()` | Dict of OpenTelemetry semantic-convention keys |
| `AgentEvent.from_dict(d)` | Reconstruct from a dict |
| `AgentEvent.from_json(s)` | Reconstruct from a JSON string |

## Enums

- `AgentEventName` — full path of every emit-able event
- `AgentEventStatus` — coarse status at emission time
- `AgentFailureClass` — failure taxonomy (`LLM_TIMEOUT`, `LLM_ERROR`, `TOOL_ERROR`, `CONTEXT_OVERFLOW`, `RATE_LIMIT`, `DEPENDENCY_FAILED`, `MEMORY_CORRUPTION`, `VALIDATION_ERROR`, `PROMPT_REJECTION`, `UNKNOWN`)

# Agent Lifecycle

Validated state machine for individual agent workers, with a tamper-evident event log. Thread-safe registry for concurrent access.

## States

```
IDLE → INITIALIZING → {WAITING_FOR_INPUT, RUNNING, FAILED}
WAITING_FOR_INPUT → {RUNNING, FAILED}
RUNNING → {COMPLETED, FAILED}
COMPLETED  # terminal
FAILED     # terminal
```

Invalid transitions raise `InvalidTransitionError`.

## Usage

```python
from techrevati.runtime import AgentRegistry, AgentStatus

registry = AgentRegistry()
worker = registry.create(role="writer", phase="draft", project_id=42)

worker.transition(AgentStatus.INITIALIZING)
worker.transition(AgentStatus.RUNNING, detail="context_budget=8k")
worker.transition(AgentStatus.COMPLETED, detail="observed=STRICT")

for event in worker.events:
    print(event.timestamp, event.status, event.detail)
```

## API

```python
class AgentRegistry:
    def create(role: str, phase: str, project_id: int | None = None) -> AgentWorker
    def get(worker_id: str) -> AgentWorker | None
    def transition(worker_id: str, status: AgentStatus, detail: str | None = None) -> AgentWorker
    def list_active() -> list[AgentWorker]
    def get_by_role_phase(role: str, phase: str) -> AgentWorker | None
    def get_by_project(project_id: int) -> list[AgentWorker]
    def clear() -> None  # testing

class AgentWorker:
    worker_id: str
    role: str
    phase: str
    project_id: int | None
    status: AgentStatus
    events: list[AgentWorkerEvent]
    retry_count: int
    last_error: dict[str, Any] | None
    provider_used: str | None
    created_at: str
    updated_at: str

    def transition(new_status: AgentStatus, detail: str | None = None) -> AgentWorkerEvent
    @property is_terminal: bool
    def to_dict() -> dict
```

`AgentWorkerEvent` records the seq, kind, status, detail, and an ISO 8601 timestamp.

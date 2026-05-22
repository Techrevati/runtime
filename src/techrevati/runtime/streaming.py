"""
Streaming — structured event stream for async model turns.

``arun_turn_stream`` reemits caller-produced text chunks as ``StreamEvent``
values plus a single terminal ``final`` event. Consumers iterate with
``async for`` and may break early — upstream cancellation is propagated
and surfaces as a ``final(status="cancelled")`` recorded on the session's
event log.

This module owns the wire format; the actual iteration loop lives in
``orchestrator.AsyncOrchestrationSession.arun_turn_stream`` so it has
access to the existing usage tracker, governance plane, and hook chain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

StreamEventType = Literal[
    "text_delta",
    "tool_call",
    "tool_result",
    "handoff",
    "final",
    "error",
]

StreamFinalStatus = Literal["completed", "cancelled", "failed"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class StreamEvent:
    """One event in a turn-level structured stream.

    Frozen so consumers can fan it out to multiple sinks without worrying
    about concurrent mutation. ``payload`` is a free-form dict whose
    shape is documented per ``type`` below.

    | type          | required payload keys              |
    |---------------|-------------------------------------|
    | text_delta    | ``delta`` (str)                     |
    | tool_call     | ``tool`` (str), ``args`` (dict)     |
    | tool_result   | ``tool`` (str), ``result`` (Any)    |
    | handoff       | ``target_role`` (str), ``reason``   |
    | final         | ``status`` (StreamFinalStatus); optional ``usage``, ``detail`` |
    | error         | ``error_type`` (str), ``message`` (str) |

    Use the classmethod constructors below for type-safe construction.
    """

    type: StreamEventType
    payload: dict[str, Any] = field(default_factory=dict)
    emitted_at: str = field(default_factory=_now_iso)

    @classmethod
    def text(cls, delta: str) -> StreamEvent:
        return cls(type="text_delta", payload={"delta": delta})

    @classmethod
    def tool_call(cls, tool: str, args: dict[str, Any] | None = None) -> StreamEvent:
        return cls(type="tool_call", payload={"tool": tool, "args": args or {}})

    @classmethod
    def tool_result(cls, tool: str, result: Any) -> StreamEvent:
        return cls(type="tool_result", payload={"tool": tool, "result": result})

    @classmethod
    def handoff(cls, target_role: str, reason: str) -> StreamEvent:
        return cls(
            type="handoff",
            payload={"target_role": target_role, "reason": reason},
        )

    @classmethod
    def final(
        cls,
        status: StreamFinalStatus,
        *,
        detail: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> StreamEvent:
        payload: dict[str, Any] = {"status": status}
        if detail is not None:
            payload["detail"] = detail
        if usage is not None:
            payload["usage"] = usage
        return cls(type="final", payload=payload)

    @classmethod
    def error(cls, error_type: str, message: str) -> StreamEvent:
        return cls(
            type="error",
            payload={"error_type": error_type, "message": message},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "payload": self.payload,
            "emitted_at": self.emitted_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


__all__ = [
    "StreamEvent",
    "StreamEventType",
    "StreamFinalStatus",
]

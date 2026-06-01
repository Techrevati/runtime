"""
Streaming — structured event stream for async model turns.

``arun_turn_stream`` reemits caller-produced text chunks as ``StreamEvent``
values plus a single terminal ``final`` event. Consumers iterate with
``async for`` and may break early — upstream cancellation is propagated
through the session's cancellation flag because the consumer is no longer
listening for another stream event.

This module owns the wire format; the actual iteration loop lives in
``orchestrator.AsyncOrchestrationSession.arun_turn_stream`` so it has
access to the existing usage tracker, governance plane, and hook chain.
"""

from __future__ import annotations

import json
from copy import deepcopy
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

_VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "text_delta",
        "tool_call",
        "tool_result",
        "handoff",
        "final",
        "error",
    }
)
_VALID_FINAL_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "failed"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _validate_non_empty_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _validate_str(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _validate_event_type(value: StreamEventType) -> StreamEventType:
    if not isinstance(value, str):
        raise TypeError("type must be a stream event type")
    if value not in _VALID_EVENT_TYPES:
        raise ValueError("type must be a valid stream event type")
    return value


def _validate_final_status(value: StreamFinalStatus) -> StreamFinalStatus:
    if not isinstance(value, str):
        raise TypeError("status must be a stream final status")
    if value not in _VALID_FINAL_STATUSES:
        raise ValueError("status must be a valid stream final status")
    return value


def _copy_payload(
    payload: dict[str, Any],
    *,
    field_name: str = "payload",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError(f"{field_name} must be a dict")
    copied = deepcopy(payload)
    for key in copied:
        if not isinstance(key, str):
            raise TypeError(f"{field_name} keys must be strings")
    return copied


def _require_payload_key(
    payload: dict[str, Any],
    key: str,
    *,
    event_type: StreamEventType,
) -> Any:
    if key not in payload:
        raise ValueError(f"{event_type} payload requires '{key}'")
    return payload[key]


def _validate_payload(
    event_type: StreamEventType,
    payload: dict[str, Any],
) -> dict[str, Any]:
    copied = _copy_payload(payload)
    if event_type == "text_delta":
        copied["delta"] = _validate_str(
            "payload.delta",
            _require_payload_key(copied, "delta", event_type=event_type),
        )
    elif event_type == "tool_call":
        copied["tool"] = _validate_non_empty_str(
            "payload.tool",
            _require_payload_key(copied, "tool", event_type=event_type),
        )
        copied["args"] = _copy_payload(
            _require_payload_key(copied, "args", event_type=event_type),
            field_name="payload.args",
        )
    elif event_type == "tool_result":
        copied["tool"] = _validate_non_empty_str(
            "payload.tool",
            _require_payload_key(copied, "tool", event_type=event_type),
        )
        _require_payload_key(copied, "result", event_type=event_type)
    elif event_type == "handoff":
        copied["target_role"] = _validate_non_empty_str(
            "payload.target_role",
            _require_payload_key(copied, "target_role", event_type=event_type),
        )
        copied["reason"] = _validate_non_empty_str(
            "payload.reason",
            _require_payload_key(copied, "reason", event_type=event_type),
        )
    elif event_type == "final":
        copied["status"] = _validate_final_status(
            _require_payload_key(copied, "status", event_type=event_type)
        )
        if "detail" in copied:
            copied["detail"] = _validate_non_empty_str(
                "payload.detail", copied["detail"]
            )
        if "usage" in copied:
            copied["usage"] = _copy_payload(copied["usage"], field_name="payload.usage")
    else:  # error
        copied["error_type"] = _validate_non_empty_str(
            "payload.error_type",
            _require_payload_key(copied, "error_type", event_type=event_type),
        )
        copied["message"] = _validate_non_empty_str(
            "payload.message",
            _require_payload_key(copied, "message", event_type=event_type),
        )
    return copied


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

    def __post_init__(self) -> None:
        event_type = _validate_event_type(self.type)
        object.__setattr__(self, "type", event_type)
        object.__setattr__(self, "payload", _validate_payload(event_type, self.payload))
        object.__setattr__(
            self, "emitted_at", _validate_non_empty_str("emitted_at", self.emitted_at)
        )

    @classmethod
    def text(cls, delta: str) -> StreamEvent:
        return cls(
            type="text_delta",
            payload={"delta": _validate_str("delta", delta)},
        )

    @classmethod
    def tool_call(cls, tool: str, args: dict[str, Any] | None = None) -> StreamEvent:
        return cls(
            type="tool_call",
            payload={
                "tool": _validate_non_empty_str("tool", tool),
                "args": _copy_payload({} if args is None else args, field_name="args"),
            },
        )

    @classmethod
    def tool_result(cls, tool: str, result: Any) -> StreamEvent:
        return cls(
            type="tool_result",
            payload={"tool": _validate_non_empty_str("tool", tool), "result": result},
        )

    @classmethod
    def handoff(cls, target_role: str, reason: str) -> StreamEvent:
        return cls(
            type="handoff",
            payload={
                "target_role": _validate_non_empty_str("target_role", target_role),
                "reason": _validate_non_empty_str("reason", reason),
            },
        )

    @classmethod
    def final(
        cls,
        status: StreamFinalStatus,
        *,
        detail: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> StreamEvent:
        payload: dict[str, Any] = {"status": _validate_final_status(status)}
        if detail is not None:
            payload["detail"] = _validate_non_empty_str("detail", detail)
        if usage is not None:
            payload["usage"] = _copy_payload(usage, field_name="usage")
        return cls(type="final", payload=payload)

    @classmethod
    def error(cls, error_type: str, message: str) -> StreamEvent:
        return cls(
            type="error",
            payload={
                "error_type": _validate_non_empty_str("error_type", error_type),
                "message": _validate_non_empty_str("message", message),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "payload": deepcopy(self.payload),
            "emitted_at": self.emitted_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


__all__ = [
    "StreamEvent",
    "StreamEventType",
    "StreamFinalStatus",
]

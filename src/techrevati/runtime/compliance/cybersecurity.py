"""
Cybersecurity hardening — robustness primitives (EU AI Act Article 15).

Article 15 requires resilience "against attempts by unauthorised third parties to
alter [an AI system's] use, outputs or performance". The runtime already ships
``PromptInjectionGuardrail`` (output-side injection detection); this module adds:

- :class:`OutputIntegrityGuardrail` — a ``Guardrail`` (``check_post``) that flags
  integrity-violation signatures in tool output (NUL bytes, ANSI/C0 control
  escape sequences used to smuggle terminal payloads, oversized output).
- :class:`InputSanitizationHook` — a ``Hook`` that scans model prompts and tool
  arguments for the same dangerous byte sequences before they reach the model /
  tool, and rejects them.

Architecture note: input sanitization is a **Hook**, not a Guardrail, because the
``Guardrail`` protocol's ``check_pre(*, role, tool)`` does not receive the tool
inputs — only hooks see ``ctx.prompt`` / ``ctx.args``. Output integrity is a
Guardrail because ``check_post`` receives the value.

.. warning::

    Engineering primitive, not legal advice. These are a first line of defense,
    not a substitute for a hardened deployment environment.
"""

from __future__ import annotations

import re
from typing import Any

from techrevati.runtime.guardrails import GuardrailOutcome

__all__ = [
    "InputSanitizationError",
    "InputSanitizationHook",
    "OutputIntegrityGuardrail",
]

# NUL byte, C0 controls except tab/newline/carriage-return (including the ESC that
# starts ANSI sequences), DEL, and C1 controls such as the 8-bit CSI (0x9b). NEL
# (0x85) is kept: it is a line break, like newline, not an escape.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f]")


def _scan_text(text: str) -> str | None:
    """Return a reason string if ``text`` looks tampered, else ``None``."""
    m = _CONTROL_CHARS.search(text)
    if m is not None:
        return f"control/escape byte {m.group(0)!r} at position {m.start()}"
    return None


def _walk(value: Any, *, max_chars: int | None) -> str | None:
    """Scan strings and bytes inside str / bytes / dict / list / tuple / set values.

    ``max_chars`` caps the payload's *total* size across all leaves. Bytes that
    decode as UTF-8 are scanned as text. Other bytes are treated as opaque binary
    (images, archives legitimately contain control bytes) and only count towards
    the size cap. Other object types are not inspected.
    """
    total = 0
    stack: list[Any] = [value]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        if isinstance(item, (bytes, bytearray, memoryview)):
            raw = bytes(item)
            total += len(raw)
            try:
                item = raw.decode("utf-8")
            except UnicodeDecodeError:
                item = ""
            else:
                total -= len(item)
        if isinstance(item, str):
            total += len(item)
            reason = _scan_text(item)
            if reason is not None:
                return reason
        elif isinstance(item, (dict, list, tuple, set, frozenset)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            if isinstance(item, dict):
                stack.extend(item.values())
                stack.extend(item.keys())
            else:
                stack.extend(item)
        if max_chars is not None and total > max_chars:
            return f"payload exceeds max_chars ({total} > {max_chars})"
    return None


class OutputIntegrityGuardrail:
    """Flag tool output carrying integrity-violation signatures (Article 15).

    Runs at ``post`` only — it inspects the tool *result*. Detects NUL bytes,
    C0 control / ANSI escape sequences, and (optionally) oversized output.
    """

    def __init__(self, *, max_chars: int | None = None, name: str = "output_integrity"):
        if not name.strip():
            raise ValueError("name must be non-empty")
        if max_chars is not None and max_chars <= 0:
            raise ValueError("max_chars must be positive or None")
        self.name = name.strip()
        self._max_chars = max_chars

    def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
        return GuardrailOutcome(allowed=True)

    def check_post(self, value: Any, *, role: str, tool: str) -> GuardrailOutcome:
        reason = _walk(value, max_chars=self._max_chars)
        if reason is None:
            return GuardrailOutcome(allowed=True)
        return GuardrailOutcome(allowed=False, reason=f"output integrity: {reason}")


class InputSanitizationError(Exception):
    """Raised by :class:`InputSanitizationHook` when input looks tampered."""


class InputSanitizationHook:
    """Reject dangerous byte sequences in model prompts / tool args (Article 15).

    A ``Hook`` (not a Guardrail) because only hooks see ``ctx.prompt`` /
    ``ctx.args``. Scans before the model call (``before_model``) and before tool
    execution (``before_tool``); raises :class:`InputSanitizationError` on a hit.

    Hooks only see what the session is given: tool arguments are scanned only when
    they are supplied to the session, so a closure-captured argument is invisible.
    """

    def __init__(
        self, *, max_chars: int | None = None, name: str = "input_sanitization"
    ):
        if not name.strip():
            raise ValueError("name must be non-empty")
        if max_chars is not None and max_chars <= 0:
            raise ValueError("max_chars must be positive or None")
        self.name = name.strip()
        self._max_chars = max_chars

    def before_model(self, ctx: Any) -> None:
        reason = _walk(ctx.prompt, max_chars=self._max_chars)
        if reason is not None:
            raise InputSanitizationError(f"prompt rejected: {reason}")

    def before_tool(self, ctx: Any) -> None:
        reason = _walk(ctx.args, max_chars=self._max_chars)
        if reason is not None:
            raise InputSanitizationError(f"tool args rejected ({ctx.tool}): {reason}")

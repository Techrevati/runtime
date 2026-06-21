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

# NUL byte, or C0 control chars except tab/newline/carriage-return, including the
# ESC (0x1b) that starts ANSI terminal escape sequences.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _scan_text(text: str, *, max_chars: int | None) -> str | None:
    """Return a reason string if ``text`` looks tampered, else ``None``."""
    if max_chars is not None and len(text) > max_chars:
        return f"payload exceeds max_chars ({len(text)} > {max_chars})"
    m = _CONTROL_CHARS.search(text)
    if m is not None:
        return f"control/escape byte {m.group(0)!r} at position {m.start()}"
    return None


def _walk(value: Any, *, max_chars: int | None) -> str | None:
    """Depth-first scan of strings inside str / dict / list / tuple structures."""
    if isinstance(value, str):
        return _scan_text(value, max_chars=max_chars)
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                reason = _scan_text(key, max_chars=max_chars)
                if reason is not None:
                    return reason
            reason = _walk(item, max_chars=max_chars)
            if reason is not None:
                return reason
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            reason = _walk(item, max_chars=max_chars)
            if reason is not None:
                return reason
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

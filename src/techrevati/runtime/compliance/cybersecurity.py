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
from collections.abc import Iterable, Iterator, Mapping
from itertools import islice
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


# A complete CSI or OSC sequence, as raw bytes.
_ESCAPE_SEQUENCE = re.compile(
    rb"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]{0,256}(?:\x07|\x1b\\)"
)
_PRINTABLE_BYTES = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
_DROP_C1 = dict.fromkeys(range(128, 160))
_MAX_BINARY_ESCAPE_SCAN = 64
_MAX_WALK_NODES = 1_000_000


def _scan_text(text: str) -> str | None:
    """Return a reason string if ``text`` looks tampered, else ``None``."""
    m = _CONTROL_CHARS.search(text)
    if m is not None:
        return f"control/escape byte {m.group(0)!r} at position {m.start()}"
    return None


def _is_text_like(raw: bytes) -> bool:
    if not raw:
        return True
    printable = sum(1 for byte in raw if byte in _PRINTABLE_BYTES)
    return printable * 2 >= len(raw)


def _scan_bytes(raw: bytes) -> str | None:
    """Scan bytes for what a terminal would act on, without rejecting binary data.

    Bytes that are valid UTF-8 and read as text get the full text scan. Anything
    else may be binary, where control bytes are ordinary, so only a complete escape
    sequence counts, and only if the value reads as text in some encoding or is
    short. Random data contains such a sequence about every 90 KB, so scanning long
    blobs would reject images at random.
    """
    text_like = _is_text_like(raw)
    if text_like:
        try:
            return _scan_text(raw.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    if not text_like and len(raw) > _MAX_BINARY_ESCAPE_SCAN:
        return None
    m = _ESCAPE_SEQUENCE.search(raw)
    if m is not None:
        return f"escape sequence {m.group(0)!r} at position {m.start()}"
    return None


def _walk(value: Any, *, max_chars: int | None) -> str | None:
    """Scan strings and bytes inside any mapping or re-iterable container.

    ``max_chars`` caps the payload's *total* size across all leaves. One-shot
    iterators are not consumed, since the caller still needs them. Other object
    types are not inspected. A payload too large to walk is rejected, not assumed
    clean.
    """
    total = 0
    visited = 0
    stack: list[Any] = [value]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        visited += 1
        if visited > _MAX_WALK_NODES:
            return f"payload exceeds {_MAX_WALK_NODES} values"
        if isinstance(item, str):
            total += len(item)
            reason = _scan_text(item)
        elif isinstance(item, (bytes, bytearray, memoryview)):
            raw = bytes(item)
            total += len(raw)
            reason = _scan_bytes(raw)
        else:
            reason = None
            if id(item) not in seen:
                seen.add(id(item))
                if isinstance(item, Mapping):
                    stack.extend(islice(item.values(), _MAX_WALK_NODES))
                    stack.extend(islice(item.keys(), _MAX_WALK_NODES))
                elif isinstance(item, Iterable) and not isinstance(item, Iterator):
                    stack.extend(islice(item, _MAX_WALK_NODES + 1))
        if reason is not None:
            return reason
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

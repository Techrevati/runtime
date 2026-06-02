"""Tests for Article 15 cybersecurity hardening (input/output integrity)."""

from __future__ import annotations

import pytest

from techrevati.runtime.compliance import (
    InputSanitizationError,
    InputSanitizationHook,
    OutputIntegrityGuardrail,
)
from techrevati.runtime.hooks import HookContext


def test_output_integrity_allows_clean_output() -> None:
    g = OutputIntegrityGuardrail()
    assert g.check_post("normal text\nwith newlines\t", role="r", tool="t").allowed
    assert g.check_pre(role="r", tool="t").allowed


def test_output_integrity_blocks_nul_and_escape() -> None:
    g = OutputIntegrityGuardrail()
    assert not g.check_post("bad\x00byte", role="r", tool="t").allowed
    assert not g.check_post("ansi \x1b[31mred", role="r", tool="t").allowed


def test_output_integrity_max_chars() -> None:
    g = OutputIntegrityGuardrail(max_chars=5)
    outcome = g.check_post("toolong", role="r", tool="t")
    assert not outcome.allowed
    assert outcome.reason is not None and "max_chars" in outcome.reason


def test_output_integrity_walks_nested_structures() -> None:
    g = OutputIntegrityGuardrail()
    assert not g.check_post({"k": ["ok", "x\x00y"]}, role="r", tool="t").allowed


def test_input_sanitization_hook_blocks_prompt() -> None:
    hook = InputSanitizationHook()
    ctx = HookContext(role="r", phase="p", prompt="evil\x00payload")
    with pytest.raises(InputSanitizationError):
        hook.before_model(ctx)


def test_input_sanitization_hook_blocks_tool_args() -> None:
    hook = InputSanitizationHook()
    ctx = HookContext(role="r", phase="p", tool="search", args={"q": "x\x1b[2J"})
    with pytest.raises(InputSanitizationError):
        hook.before_tool(ctx)


def test_input_sanitization_hook_allows_clean() -> None:
    hook = InputSanitizationHook()
    ctx = HookContext(role="r", phase="p", prompt="hello", args={"q": "world"})
    hook.before_model(ctx)  # no raise
    hook.before_tool(ctx)  # no raise


def test_validation_errors() -> None:
    with pytest.raises(ValueError):
        OutputIntegrityGuardrail(max_chars=0)
    with pytest.raises(ValueError):
        InputSanitizationHook(name="  ")

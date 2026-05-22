"""Tests for techrevati.runtime.hooks — interceptor chain + built-ins."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from techrevati.runtime import (
    AgentSession,
    HookBudgetExceededError,
    HookContext,
    LogModelIOHook,
    RedactPIIHook,
    TokenBudgetCheckHook,
)
from techrevati.runtime.hooks import (
    _is_coroutine_function,
    arun_after_model,
    arun_before_model,
    run_after_model,
    run_after_tool,
    run_before_model,
)

# ---------------------------------------------------------------------------
# HookContext
# ---------------------------------------------------------------------------


def test_hook_context_defaults() -> None:
    ctx = HookContext(role="writer", phase="draft")
    assert ctx.role == "writer"
    assert ctx.phase == "draft"
    assert ctx.model == ""
    assert ctx.prompt is None
    assert ctx.tool == ""
    assert ctx.args == {}
    assert ctx.extra == {}


def test_hook_context_mutable_prompt_propagates_via_closure() -> None:
    """Caller closes over ctx.prompt — hooks redact, closure sees new value."""
    ctx = HookContext(role="writer", phase="draft", prompt="hello")

    def call() -> str:
        return f"got: {ctx.prompt}"

    class UpperHook:
        name = "upper"

        def before_model(self, c: HookContext) -> None:
            c.prompt = str(c.prompt).upper()

    run_before_model([UpperHook()], ctx)
    assert call() == "got: HELLO"


# ---------------------------------------------------------------------------
# Sync dispatcher: chain order + transform semantics
# ---------------------------------------------------------------------------


class _AppendHook:
    def __init__(self, tag: str) -> None:
        self.name = f"append_{tag}"
        self._tag = tag

    def before_model(self, ctx: HookContext) -> None:
        ctx.prompt = f"{ctx.prompt}|{self._tag}"

    def after_model(self, ctx: HookContext, result: Any) -> Any:
        return f"{result}|{self._tag}"


def test_before_model_chain_left_to_right() -> None:
    ctx = HookContext(role="r", phase="p", prompt="start")
    hooks = [_AppendHook("a"), _AppendHook("b"), _AppendHook("c")]
    run_before_model(hooks, ctx)
    # Third hook sees the result of the first two.
    assert ctx.prompt == "start|a|b|c"


def test_after_model_chain_threads_result() -> None:
    ctx = HookContext(role="r", phase="p")
    hooks = [_AppendHook("x"), _AppendHook("y")]
    result = run_after_model(hooks, ctx, "result")
    assert result == "result|x|y"


def test_hook_without_method_is_skipped() -> None:
    """A hook that only implements after_tool is silently skipped for before_model."""

    class OnlyAfterTool:
        name = "only_after_tool"

        def after_tool(self, ctx: HookContext, result: Any) -> Any:
            return f"tool:{result}"

    ctx = HookContext(role="r", phase="p")
    run_before_model([OnlyAfterTool()], ctx)  # no error
    result = run_after_tool([OnlyAfterTool()], ctx, "x")
    assert result == "tool:x"


def test_async_hook_skipped_on_sync_path() -> None:
    """An async hook on the sync chain is skipped, not called."""

    calls: list[str] = []

    class AsyncOnly:
        name = "async_only"

        async def abefore_model(self, ctx: HookContext) -> None:
            calls.append("abefore_model")

        async def aafter_model(self, ctx: HookContext, result: Any) -> Any:
            calls.append("aafter_model")
            return result

    ctx = HookContext(role="r", phase="p")
    run_before_model([AsyncOnly()], ctx)
    result = run_after_model([AsyncOnly()], ctx, "x")
    assert result == "x"
    assert calls == []  # async hooks never fired


# ---------------------------------------------------------------------------
# Async dispatcher: chain order + mixed sync/async hooks
# ---------------------------------------------------------------------------


class _AsyncAppendHook:
    def __init__(self, tag: str) -> None:
        self.name = f"aappend_{tag}"
        self._tag = tag

    async def abefore_model(self, ctx: HookContext) -> None:
        ctx.prompt = f"{ctx.prompt}|{self._tag}"

    async def aafter_model(self, ctx: HookContext, result: Any) -> Any:
        return f"{result}|{self._tag}"


@pytest.mark.asyncio
async def test_async_chain_threads_result() -> None:
    ctx = HookContext(role="r", phase="p", prompt="s")
    hooks = [_AsyncAppendHook("a"), _AsyncAppendHook("b")]
    await arun_before_model(hooks, ctx)
    result = await arun_after_model(hooks, ctx, "r")
    assert ctx.prompt == "s|a|b"
    assert result == "r|a|b"


@pytest.mark.asyncio
async def test_async_chain_handles_mixed_sync_and_async_hooks() -> None:
    ctx = HookContext(role="r", phase="p", prompt="s")
    hooks = [
        _AppendHook("sync1"),
        _AsyncAppendHook("async1"),
        _AppendHook("sync2"),
    ]
    await arun_before_model(hooks, ctx)
    assert ctx.prompt == "s|sync1|async1|sync2"


# ---------------------------------------------------------------------------
# RedactPIIHook
# ---------------------------------------------------------------------------


def test_redact_pii_default_patterns_string() -> None:
    h = RedactPIIHook()
    ctx = HookContext(
        role="r",
        phase="p",
        prompt="email me at alice@example.com or SSN 123-45-6789",
    )
    h.before_model(ctx)
    assert "alice@example.com" not in ctx.prompt
    assert "123-45-6789" not in ctx.prompt
    assert "[REDACTED]" in ctx.prompt


def test_redact_pii_walks_dict_and_list_messages() -> None:
    h = RedactPIIHook()
    ctx = HookContext(
        role="r",
        phase="p",
        prompt=[
            {"role": "user", "content": "ping me at bob@x.io"},
            {"role": "system", "content": "ok"},
        ],
    )
    h.before_model(ctx)
    assert ctx.prompt[0]["content"] == "ping me at [REDACTED]"
    assert ctx.prompt[1]["content"] == "ok"


def test_redact_pii_skips_unknown_shapes() -> None:
    h = RedactPIIHook()
    ctx = HookContext(role="r", phase="p", prompt=42)
    h.before_model(ctx)
    assert ctx.prompt == 42  # unchanged


def test_redact_pii_custom_patterns() -> None:
    h = RedactPIIHook(patterns=[r"secret-\w+"])
    ctx = HookContext(role="r", phase="p", prompt="hello secret-foo world")
    h.before_model(ctx)
    assert ctx.prompt == "hello [REDACTED] world"


def test_redact_pii_rejects_empty_patterns() -> None:
    with pytest.raises(ValueError, match="at least one pattern"):
        RedactPIIHook(patterns=[])


def test_redact_pii_after_model_redacts_output() -> None:
    h = RedactPIIHook(patterns=[r"\d{3}-\d{2}-\d{4}"])
    ctx = HookContext(role="r", phase="p")
    out = h.after_model(ctx, "model leaked 111-22-3333")
    assert out == "model leaked [REDACTED]"


# ---------------------------------------------------------------------------
# LogModelIOHook
# ---------------------------------------------------------------------------


def test_log_model_io_hook_emits_input_and_output(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = LogModelIOHook(level=logging.DEBUG)
    ctx = HookContext(role="r", phase="p", model="m", prompt="hi")
    with caplog.at_level(logging.DEBUG, logger="techrevati.runtime.hooks"):
        h.before_model(ctx)
        result = h.after_model(ctx, "out")
    assert result == "out"
    messages = [rec.message for rec in caplog.records]
    assert "model_input" in messages
    assert "model_output" in messages


def test_log_model_io_hook_truncates(caplog: pytest.LogCaptureFixture) -> None:
    h = LogModelIOHook(max_chars=10)
    ctx = HookContext(role="r", phase="p", model="m", prompt="x" * 50)
    with caplog.at_level(logging.INFO, logger="techrevati.runtime.hooks"):
        h.before_model(ctx)
    rec = next(r for r in caplog.records if r.message == "model_input")
    assert rec.prompt.endswith("…(truncated)")  # type: ignore[attr-defined]


def test_log_model_io_hook_rejects_zero_max_chars() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        LogModelIOHook(max_chars=0)


def test_log_model_io_hook_respects_include_toggles(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = LogModelIOHook(include_prompt=False, include_result=False)
    ctx = HookContext(role="r", phase="p", model="m", prompt="hi")
    with caplog.at_level(logging.DEBUG, logger="techrevati.runtime.hooks"):
        h.before_model(ctx)
        h.after_model(ctx, "out")
    assert all(r.message not in {"model_input", "model_output"} for r in caplog.records)


# ---------------------------------------------------------------------------
# TokenBudgetCheckHook
# ---------------------------------------------------------------------------


def test_token_budget_hook_under_limit_passes() -> None:
    h = TokenBudgetCheckHook(token_limit=100)
    ctx = HookContext(role="r", phase="p", model="m", prompt="short")
    h.before_model(ctx)  # no raise


def test_token_budget_hook_over_limit_raises() -> None:
    h = TokenBudgetCheckHook(token_limit=2)
    ctx = HookContext(role="r", phase="p", model="m", prompt="x" * 100)
    with pytest.raises(HookBudgetExceededError) as ei:
        h.before_model(ctx)
    assert ei.value.limit == 2
    assert ei.value.estimated > 2
    assert ei.value.model == "m"


def test_token_budget_hook_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBudgetCheckHook(token_limit=0)


def test_token_budget_hook_accepts_custom_estimator() -> None:
    h = TokenBudgetCheckHook(token_limit=5, estimator=lambda _: 7)
    ctx = HookContext(role="r", phase="p", model="m", prompt="anything")
    with pytest.raises(HookBudgetExceededError):
        h.before_model(ctx)


# ---------------------------------------------------------------------------
# Integration: hooks wired through AgentSession.run_turn / arun_turn
# ---------------------------------------------------------------------------


def test_run_turn_invokes_before_and_after_model_hooks() -> None:
    ctx = HookContext(role="writer", phase="draft", prompt="start")
    sess = AgentSession(
        role="writer",
        phase="draft",
        hooks=[_AppendHook("h1"), _AppendHook("h2")],
    )
    with sess.session() as session:

        def call() -> str:
            return f"got({ctx.prompt})"

        result, _ = session.run_turn(call, model="m", hook_ctx=ctx)
    # before_model chain mutated ctx.prompt → fn closes over it
    # after_model chain wraps the result
    assert result == "got(start|h1|h2)|h1|h2"


def test_run_tool_invokes_before_and_after_tool_hooks() -> None:
    after_calls: list[Any] = []

    class ToolHook:
        name = "tool_hook"

        def before_tool(self, ctx: HookContext) -> None:
            ctx.args["mutated"] = True

        def after_tool(self, ctx: HookContext, result: Any) -> Any:
            after_calls.append(result)
            return {"wrapped": result}

    args: dict[str, Any] = {"x": 1}
    ctx = HookContext(role="writer", phase="draft", args=args)
    sess = AgentSession(role="writer", phase="draft", hooks=[ToolHook()])
    with sess.session() as session:
        out = session.run_tool("calc", lambda: {"sum": args["x"]}, hook_ctx=ctx)
    assert args.get("mutated") is True
    assert out == {"wrapped": {"sum": 1}}
    assert after_calls == [{"sum": 1}]


@pytest.mark.asyncio
async def test_arun_turn_invokes_async_hook_chain() -> None:
    ctx = HookContext(role="writer", phase="draft", prompt="start")
    sess = AgentSession(
        role="writer",
        phase="draft",
        hooks=[_AsyncAppendHook("a"), _AsyncAppendHook("b")],
    )
    async with sess.asession() as session:

        async def call() -> str:
            return f"got({ctx.prompt})"

        result, _ = await session.arun_turn(call, model="m", hook_ctx=ctx)
    assert result == "got(start|a|b)|a|b"


@pytest.mark.asyncio
async def test_arun_tool_invokes_async_hook_chain() -> None:
    seen: list[str] = []

    class TraceTool:
        name = "trace"

        async def abefore_tool(self, ctx: HookContext) -> None:
            seen.append(f"pre:{ctx.tool}")

        async def aafter_tool(self, ctx: HookContext, result: Any) -> Any:
            seen.append(f"post:{ctx.tool}:{result}")
            return result * 2

    sess = AgentSession(role="writer", phase="draft", hooks=[TraceTool()])
    async with sess.asession() as session:

        async def tool() -> int:
            return 21

        out = await session.arun_tool("answer", tool)
    assert out == 42
    assert seen == ["pre:answer", "post:answer:21"]


def test_run_turn_synthesizes_hook_context_when_omitted() -> None:
    """Without hook_ctx the session synthesizes one — hooks still fire."""

    seen: list[str] = []

    class SpyHook:
        name = "spy"

        def before_model(self, ctx: HookContext) -> None:
            seen.append(f"before:{ctx.role}:{ctx.phase}:{ctx.model}")

    sess = AgentSession(role="writer", phase="draft", hooks=[SpyHook()])
    with sess.session() as session:
        session.run_turn(lambda: "x", model="m")
    assert seen == ["before:writer:draft:m"]


# ---------------------------------------------------------------------------
# Internal helper: coroutine detection
# ---------------------------------------------------------------------------


def test_is_coroutine_function_detects_async_def() -> None:
    async def afn() -> None:
        return None

    def sfn() -> None:
        return None

    assert _is_coroutine_function(afn) is True
    assert _is_coroutine_function(sfn) is False

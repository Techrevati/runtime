"""
Hooks — interceptor chain that *mutates* model / tool inputs and outputs.

Hooks differ from ``EventSink`` (which observes only): each hook may
inspect and rewrite the data flowing through a turn or a tool call.
The orchestrator runs hooks left-to-right around every ``run_turn`` /
``arun_turn`` and ``run_tool`` / ``arun_tool`` call, so a later hook
always sees the output of the previous one.

The two built-in transformations are:

- **before_model / before_tool** — receive a mutable ``HookContext``;
  mutate ``ctx.prompt`` (model) or ``ctx.args`` (tool) in place.
  Callers expose the mutable container via the ``prompt_ctx=`` /
  ``args_ctx=`` kwargs on the session methods; if not supplied, the
  orchestrator synthesizes a no-op context so hooks still fire.

- **after_model / after_tool** — receive the model/tool result and
  return a (possibly new) replacement value. Returning ``None`` is
  taken literally — return the original value to leave it unchanged.

Hooks are intentionally caller-supplied, not "smart" defaults: the
runtime ships three reference implementations (``RedactPIIHook``,
``LogModelIOHook``, ``TokenBudgetCheckHook``) and applications compose
their own. Heavy/IO-bound work belongs in ``AsyncHook`` so it does not
block the event loop.

This module is zero-dep. Built-ins use stdlib only.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("techrevati.runtime.hooks")
logger.addHandler(logging.NullHandler())


# ---------------------------------------------------------------------------
# Context object
# ---------------------------------------------------------------------------


@dataclass
class HookContext:
    """Mutable context passed through the hook chain.

    The same instance flows left-to-right through the chain; later hooks
    see earlier hooks' mutations. The orchestrator does not introspect
    any field except for logging — caller controls the shape of
    ``prompt`` and ``args``.

    Fields:
        role: the session role (``AgentSession.role``).
        phase: the session phase (``AgentSession.phase``).
        model: the model name passed to ``run_turn`` / ``arun_turn``.
            Empty string for tool hooks.
        prompt: opaque caller-supplied model input. Hooks redact / log /
            mutate this in place; the caller's coro_factory closes over
            it so the model call sees the post-hook value.
        tool: the tool name for ``run_tool`` / ``arun_tool``. Empty
            string for model hooks.
        args: caller-supplied tool input dict. Hooks may mutate keys.
        extra: free-form dict for caller-defined keys (e.g. correlation
            id, trace context).
    """

    role: str
    phase: str
    model: str = ""
    prompt: Any = None
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class Hook(Protocol):
    """Sync interceptor.

    Implementations override any subset of the four lifecycle methods —
    the dispatcher uses ``hasattr`` to skip unimplemented ones, so a
    hook that only cares about ``after_tool`` need not provide stubs.

    The ``name`` attribute labels the hook in logs and events; default
    to the class name if not set explicitly.
    """

    name: str

    # All methods are optional via getattr dispatch. Signatures:
    #
    # def before_model(self, ctx: HookContext) -> None: ...
    # def after_model(self, ctx: HookContext, result: Any) -> Any: ...
    # def before_tool(self, ctx: HookContext) -> None: ...
    # def after_tool(self, ctx: HookContext, result: Any) -> Any: ...
    # def before_handoff(self, ctx: HookContext) -> None: ...


@runtime_checkable
class AsyncHook(Protocol):
    """Async sibling of ``Hook``.

    Async sessions dispatch both — sync hooks run inline, async hooks
    are awaited. Optional methods (override the ones you need):

    - ``async def abefore_model(self, ctx: HookContext) -> None``
    - ``async def aafter_model(self, ctx: HookContext, result: Any) -> Any``
    - ``async def abefore_tool(self, ctx: HookContext) -> None``
    - ``async def aafter_tool(self, ctx: HookContext, result: Any) -> Any``
    - ``async def abefore_handoff(self, ctx: HookContext) -> None``
    """

    name: str


HookLike = Hook | AsyncHook


def _hook_label(hook: HookLike) -> str:
    return getattr(hook, "name", type(hook).__name__)


# ---------------------------------------------------------------------------
# Dispatchers
# ---------------------------------------------------------------------------


def _dispatch_sync(
    hooks: Sequence[HookLike],
    method: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Call a sync hook method on every hook that defines it.

    Async hooks are skipped silently (only the async dispatcher should
    see them). Hooks that raise are NOT caught — they short-circuit the
    chain so a misconfigured guardrail-style hook can block the turn.
    """
    for hook in hooks:
        fn = getattr(hook, method, None)
        if fn is None:
            continue
        # Skip async coroutine functions on the sync path; arun_*
        # dispatchers handle them. ``inspect.iscoroutinefunction``
        # would be more correct but imports + cost; ``__code__`` flag
        # check is cheaper for the hot path. Fall back if anything
        # unusual.
        if _is_coroutine_function(fn):
            continue
        fn(*args, **kwargs)


def _dispatch_sync_transform(
    hooks: Sequence[HookLike],
    method: str,
    ctx: HookContext,
    value: Any,
) -> Any:
    """Run a left-to-right transformer chain; each hook may return a new value."""
    current = value
    for hook in hooks:
        fn = getattr(hook, method, None)
        if fn is None:
            continue
        if _is_coroutine_function(fn):
            continue
        current = fn(ctx, current)
    return current


async def _dispatch_async(
    hooks: Sequence[HookLike],
    sync_method: str,
    async_method: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Call sync OR async observer method on every hook that defines either."""
    for hook in hooks:
        async_fn = getattr(hook, async_method, None)
        if async_fn is not None:
            await async_fn(*args, **kwargs)
            continue
        sync_fn = getattr(hook, sync_method, None)
        if sync_fn is None:
            continue
        if _is_coroutine_function(sync_fn):
            # Defensive: someone aliased the async method onto the sync
            # name. Await it.
            await sync_fn(*args, **kwargs)
            continue
        sync_fn(*args, **kwargs)


async def _dispatch_async_transform(
    hooks: Sequence[HookLike],
    sync_method: str,
    async_method: str,
    ctx: HookContext,
    value: Any,
) -> Any:
    """Async transformer chain; honors both sync and async hooks."""
    current = value
    for hook in hooks:
        async_fn = getattr(hook, async_method, None)
        if async_fn is not None:
            current = await async_fn(ctx, current)
            continue
        sync_fn = getattr(hook, sync_method, None)
        if sync_fn is None:
            continue
        if _is_coroutine_function(sync_fn):
            current = await sync_fn(ctx, current)
            continue
        current = sync_fn(ctx, current)
    return current


def _is_coroutine_function(fn: Any) -> bool:
    """True if ``fn`` is an ``async def`` callable.

    Delegates to ``inspect.iscoroutinefunction`` so bound methods,
    functools.partial, and other wrappers are handled correctly. Hook
    dispatch is at most once per turn/tool call — not on a chunk-level
    hot path — so the cost is negligible.
    """
    import inspect

    return inspect.iscoroutinefunction(fn)


# ---------------------------------------------------------------------------
# Public chain runners — used by orchestrator
# ---------------------------------------------------------------------------


def run_before_model(hooks: Sequence[HookLike], ctx: HookContext) -> None:
    _dispatch_sync(hooks, "before_model", ctx)


def run_after_model(hooks: Sequence[HookLike], ctx: HookContext, result: Any) -> Any:
    return _dispatch_sync_transform(hooks, "after_model", ctx, result)


def run_before_tool(hooks: Sequence[HookLike], ctx: HookContext) -> None:
    _dispatch_sync(hooks, "before_tool", ctx)


def run_after_tool(hooks: Sequence[HookLike], ctx: HookContext, result: Any) -> Any:
    return _dispatch_sync_transform(hooks, "after_tool", ctx, result)


def run_before_handoff(hooks: Sequence[HookLike], ctx: HookContext) -> None:
    _dispatch_sync(hooks, "before_handoff", ctx)


async def arun_before_model(hooks: Sequence[HookLike], ctx: HookContext) -> None:
    await _dispatch_async(hooks, "before_model", "abefore_model", ctx)


async def arun_after_model(
    hooks: Sequence[HookLike], ctx: HookContext, result: Any
) -> Any:
    return await _dispatch_async_transform(
        hooks, "after_model", "aafter_model", ctx, result
    )


async def arun_before_tool(hooks: Sequence[HookLike], ctx: HookContext) -> None:
    await _dispatch_async(hooks, "before_tool", "abefore_tool", ctx)


async def arun_after_tool(
    hooks: Sequence[HookLike], ctx: HookContext, result: Any
) -> Any:
    return await _dispatch_async_transform(
        hooks, "after_tool", "aafter_tool", ctx, result
    )


async def arun_before_handoff(hooks: Sequence[HookLike], ctx: HookContext) -> None:
    await _dispatch_async(hooks, "before_handoff", "abefore_handoff", ctx)


# ---------------------------------------------------------------------------
# Built-in hooks
# ---------------------------------------------------------------------------


_DEFAULT_PII_PATTERNS: tuple[str, ...] = (
    # US SSN
    r"\b\d{3}-\d{2}-\d{4}\b",
    # Email
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    # Credit card (loose: 13–19 digits with optional separators)
    r"\b(?:\d[ -]*?){13,19}\b",
    # IPv4
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    # Bearer / API key heuristic (40+ char hex/base64-ish blobs)
    r"\b[A-Za-z0-9_\-]{40,}\b",
)


class RedactPIIHook:
    """Redact PII patterns from text-bearing ``ctx.prompt`` before model calls.

    Operates on:

    - ``ctx.prompt`` when it is a ``str`` — replaces matches with
      ``replacement`` (default ``"[REDACTED]"``).
    - ``ctx.prompt`` when it is a ``dict`` — walks string leaves.
    - ``ctx.prompt`` when it is a list of dicts (OpenAI messages
      shape) — walks each message's ``content`` field.

    Other shapes are skipped silently — caller can supply a custom hook
    when the schema is exotic. The hook is **best-effort**: it is a
    first-line defense, not a substitute for a dedicated PII scrubber
    (see ``docs/patterns/hooks.md`` for the discussion).

    Also redacts ``after_model(result)`` when the result is a string,
    so log sinks downstream cannot leak content the upstream redactor
    caught coming in.
    """

    def __init__(
        self,
        *,
        patterns: Sequence[str] | None = None,
        replacement: str = "[REDACTED]",
        name: str = "redact_pii",
    ) -> None:
        raw = tuple(patterns) if patterns is not None else _DEFAULT_PII_PATTERNS
        if not raw:
            raise ValueError("RedactPIIHook requires at least one pattern")
        self.name = name
        self._replacement = replacement
        self._compiled = re.compile("|".join(f"(?:{p})" for p in raw))

    def _scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._compiled.sub(self._replacement, value)
        if isinstance(value, dict):
            return {k: self._scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._scrub(item) for item in value]
        return value

    def before_model(self, ctx: HookContext) -> None:
        ctx.prompt = self._scrub(ctx.prompt)

    def after_model(self, ctx: HookContext, result: Any) -> Any:
        return self._scrub(result)

    def before_tool(self, ctx: HookContext) -> None:
        ctx.args = self._scrub(ctx.args) if isinstance(ctx.args, dict) else ctx.args


class LogModelIOHook:
    """Log model input + output via a stdlib logger.

    Useful for development and audit dumps. Defaults to ``logger.info``
    at the ``techrevati.runtime.hooks`` logger; pass ``logger=`` to
    redirect. ``include_prompt`` / ``include_result`` are toggles so
    production deployments can keep one side off (e.g. log inputs only
    for debugging, never log outputs).

    The hook truncates payloads above ``max_chars`` (default 4000) and
    suffixes ``"…(truncated)"`` so a runaway 100k-token blob does not
    flood the log pipeline.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        level: int = logging.INFO,
        include_prompt: bool = True,
        include_result: bool = True,
        max_chars: int = 4000,
        name: str = "log_model_io",
    ) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.name = name
        self._logger = logger or logging.getLogger("techrevati.runtime.hooks")
        self._level = level
        self._include_prompt = include_prompt
        self._include_result = include_result
        self._max_chars = max_chars

    def _fmt(self, value: Any) -> str:
        text = value if isinstance(value, str) else repr(value)
        if len(text) > self._max_chars:
            return text[: self._max_chars] + "…(truncated)"
        return text

    def before_model(self, ctx: HookContext) -> None:
        if not self._include_prompt:
            return
        self._logger.log(
            self._level,
            "model_input",
            extra={
                "role": ctx.role,
                "phase": ctx.phase,
                "model": ctx.model,
                "prompt": self._fmt(ctx.prompt),
            },
        )

    def after_model(self, ctx: HookContext, result: Any) -> Any:
        if self._include_result:
            self._logger.log(
                self._level,
                "model_output",
                extra={
                    "role": ctx.role,
                    "phase": ctx.phase,
                    "model": ctx.model,
                    "result": self._fmt(result),
                },
            )
        return result


class HookBudgetExceededError(Exception):
    """Raised by ``TokenBudgetCheckHook`` when an estimate exceeds the cap.

    Catchable inside ``arun_turn`` so the recovery loop can react (e.g.
    smaller context budget). Use ``GovernancePlane`` with
    ``MaxBudgetLimit(on_breach="terminate")`` if you want a hard stop
    that bypasses recovery.
    """

    def __init__(self, *, estimated: int, limit: int, model: str) -> None:
        self.estimated = estimated
        self.limit = limit
        self.model = model
        super().__init__(
            f"estimated tokens for model={model!r}: {estimated} > limit={limit}"
        )


class TokenBudgetCheckHook:
    """Pre-flight token-budget guard.

    Estimates input tokens for the configured model using a caller-
    supplied estimator (e.g. ``lambda p: len(str(p)) // 4`` as a coarse
    fallback, or a real tokenizer). Raises ``HookBudgetExceededError``
    if the estimate exceeds ``token_limit``.

    This is an **alternative** to ``UsageLimits`` for the case where the
    budget needs to be enforced *before* the model call rather than
    *after* the response comes back. The two compose: keep the
    pre-flight check tight (cheap, catches obvious mistakes) and let
    ``UsageLimits`` enforce the cumulative quota across many turns.
    """

    def __init__(
        self,
        *,
        token_limit: int,
        estimator: Any | None = None,
        name: str = "token_budget_check",
    ) -> None:
        if token_limit <= 0:
            raise ValueError("token_limit must be positive")
        self.name = name
        self.token_limit = token_limit
        # Default estimator: 4-chars-per-token heuristic. Good enough as
        # a guardrail; callers running production should pass a real
        # tokenizer (e.g. tiktoken).
        self._estimator = estimator or (lambda p: max(0, len(str(p)) // 4))

    def before_model(self, ctx: HookContext) -> None:
        estimated = int(self._estimator(ctx.prompt))
        if estimated > self.token_limit:
            raise HookBudgetExceededError(
                estimated=estimated,
                limit=self.token_limit,
                model=ctx.model,
            )


__all__ = [
    "AsyncHook",
    "Hook",
    "HookBudgetExceededError",
    "HookContext",
    "HookLike",
    "LogModelIOHook",
    "RedactPIIHook",
    "TokenBudgetCheckHook",
    "arun_after_model",
    "arun_after_tool",
    "arun_before_handoff",
    "arun_before_model",
    "arun_before_tool",
    "run_after_model",
    "run_after_tool",
    "run_before_handoff",
    "run_before_model",
    "run_before_tool",
]

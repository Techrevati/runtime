"""AsyncGuardrail protocol + AsyncOrchestrationSession integration."""

from __future__ import annotations

import asyncio
import logging

import pytest

from techrevati.runtime import (
    AgentSession,
    AsyncGuardrail,
    Guardrail,
    GuardrailOutcome,
    GuardrailViolatedError,
)


class _AsyncBlockingGuardrail:
    """Async guardrail that simulates I/O before deciding."""

    name = "async_block"

    def __init__(self, *, block: bool) -> None:
        self._block = block
        self.called: list[str] = []

    async def acheck_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
        await asyncio.sleep(0)  # yield to event loop
        self.called.append(f"pre:{tool}")
        if self._block:
            return GuardrailOutcome(allowed=False, reason="async pre-block")
        return GuardrailOutcome(allowed=True)

    async def acheck_post(
        self, value: object, *, role: str, tool: str
    ) -> GuardrailOutcome:
        await asyncio.sleep(0)
        self.called.append(f"post:{tool}")
        return GuardrailOutcome(allowed=True)


class _SyncAllow:
    """Sync guardrail that always allows. Used in mixed lists."""

    name = "sync_allow"

    def check_pre(self, *, role: str, tool: str) -> GuardrailOutcome:
        return GuardrailOutcome(allowed=True)

    def check_post(self, value: object, *, role: str, tool: str) -> GuardrailOutcome:
        return GuardrailOutcome(allowed=True)


def test_async_guardrail_implements_protocol():
    g = _AsyncBlockingGuardrail(block=False)
    assert isinstance(g, AsyncGuardrail)
    assert not isinstance(g, Guardrail)  # purely async


def test_sync_guardrail_is_not_async_guardrail():
    g = _SyncAllow()
    assert isinstance(g, Guardrail)
    assert not isinstance(g, AsyncGuardrail)


@pytest.mark.asyncio
async def test_async_session_awaits_async_guardrail():
    g = _AsyncBlockingGuardrail(block=False)
    sess = AgentSession(role="r", phase="p", guardrails=[g])

    async def _coro() -> str:
        return "ok"

    async with sess.asession() as session:
        result = await session.arun_tool("inspect", _coro)
    assert result == "ok"
    assert g.called == ["pre:inspect", "post:inspect"]


@pytest.mark.asyncio
async def test_async_session_async_guardrail_blocks():
    g = _AsyncBlockingGuardrail(block=True)
    sess = AgentSession(role="r", phase="p", guardrails=[g])

    async def _coro() -> str:
        return "ok"

    with pytest.raises(GuardrailViolatedError) as exc_info:
        async with sess.asession() as session:
            await session.arun_tool("inspect", _coro)
    assert exc_info.value.violations[0].guardrail == "async_block"


@pytest.mark.asyncio
async def test_async_session_mixed_sync_and_async_guardrails():
    sync_g = _SyncAllow()
    async_g = _AsyncBlockingGuardrail(block=False)
    sess = AgentSession(role="r", phase="p", guardrails=[sync_g, async_g])

    async def _coro() -> str:
        return "ok"

    async with sess.asession() as session:
        assert await session.arun_tool("inspect", _coro) == "ok"
    assert async_g.called == ["pre:inspect", "post:inspect"]


def test_sync_session_skips_async_guardrail_with_warning(caplog):
    g = _AsyncBlockingGuardrail(block=True)  # would block if awaited
    sess = AgentSession(role="r", phase="p", guardrails=[g])

    with caplog.at_level(logging.WARNING, logger="techrevati.runtime.guardrails"):
        with sess.session() as session:
            # Sync session skips the async guardrail; the tool runs.
            assert session.run_tool("inspect", lambda: "ok") == "ok"

    # And a warning was emitted about the skip
    matching = [r for r in caplog.records if "AsyncGuardrail" in r.message]
    assert matching, "expected a warning about skipped AsyncGuardrail"
    # Async guardrail was never invoked
    assert g.called == []

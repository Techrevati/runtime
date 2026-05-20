"""Tests for ``AsyncOrchestrationSession.arun_parallel_tools``.

Structured concurrency contract: results come back in input order,
any child failure cancels its siblings and surfaces an ExceptionGroup,
timeout maps to TurnTimeoutError.
"""

from __future__ import annotations

import asyncio

import pytest

from techrevati.runtime import AgentSession, TurnTimeoutError


@pytest.mark.asyncio
async def test_parallel_tools_returns_results_in_order() -> None:
    orch = AgentSession(role="writer", phase="draft")

    async def make_a() -> str:
        await asyncio.sleep(0.01)
        return "a"

    async def make_b() -> str:
        return "b"

    async def make_c() -> str:
        await asyncio.sleep(0)
        return "c"

    async with orch.asession() as session:
        results = await session.arun_parallel_tools([make_a, make_b, make_c])
    assert results == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_parallel_tools_empty_input_returns_empty_list() -> None:
    orch = AgentSession(role="writer", phase="draft")
    async with orch.asession() as session:
        assert await session.arun_parallel_tools([]) == []


@pytest.mark.asyncio
async def test_parallel_tools_failure_cancels_siblings() -> None:
    """One child raising must cancel the others and re-raise as ExceptionGroup."""
    orch = AgentSession(role="writer", phase="draft")
    sibling_was_cancelled = False

    async def sibling_long() -> str:
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            nonlocal_marker()
            raise
        return "should-not-arrive"

    def nonlocal_marker() -> None:
        nonlocal sibling_was_cancelled
        sibling_was_cancelled = True

    async def failing() -> str:
        await asyncio.sleep(0)
        raise RuntimeError("boom")

    async with orch.asession() as session:
        with pytest.raises(BaseExceptionGroup) as ei:
            await session.arun_parallel_tools([sibling_long, failing])
    # Inner exception must be the RuntimeError we raised.
    assert any(isinstance(e, RuntimeError) for e in ei.value.exceptions)
    assert sibling_was_cancelled


@pytest.mark.asyncio
async def test_parallel_tools_timeout_maps_to_turn_timeout_error() -> None:
    orch = AgentSession(role="writer", phase="draft")

    async def slow() -> str:
        await asyncio.sleep(5.0)
        return "x"

    async with orch.asession() as session:
        with pytest.raises(TurnTimeoutError):
            await session.arun_parallel_tools([slow], timeout=0.05)

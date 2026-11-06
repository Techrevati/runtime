"""Regressions and 0.5.0 session features: settlement order, replay, streaming."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
from collections.abc import AsyncIterator

import pytest

from techrevati.runtime import (
    AgentSession,
    InMemorySaver,
    ModelPricing,
    StaticProviderRouter,
    TokenBucket,
    UsageLimitExceededError,
    UsageLimits,
    UsageSnapshot,
    register_pricing,
)
from techrevati.runtime.agent_lifecycle import AgentStatus
from techrevati.runtime.governance import (
    GovernanceBreachError,
    GovernancePlane,
    MaxBudgetLimit,
    MaxConsecutiveFailuresLimit,
    MaxRepeatedToolCallsLimit,
    tool_call_signature,
)
from techrevati.runtime.orchestrator import (
    MaxIterationsExceededError,
    PermissionDeniedError,
    ToolTimeoutError,
    TurnTimeoutError,
)
from techrevati.runtime.permissions import PermissionOutcome
from techrevati.runtime.rate_limit import RateLimiter
from techrevati.runtime.usage_tracking import BudgetExceededError

MODEL = "regress-model"
ONE_DOLLAR = UsageSnapshot(input_tokens=1000, output_tokens=0)


@pytest.fixture(autouse=True)
def _pricing() -> None:
    register_pricing(
        MODEL,
        ModelPricing(input_per_million=1000.0, output_per_million=0.0),
        on_conflict="overwrite",
    )


def _agent(**kwargs: object) -> AgentSession:
    return AgentSession(role="r", phase="p", **kwargs)  # type: ignore[arg-type]


# -- settlement order ---------------------------------------------------------


def test_budget_breach_keeps_the_paid_turn_replayable() -> None:
    saver = InMemorySaver()
    calls: list[int] = []
    agent = _agent(budget_usd=0.5, enforce_budget=True, saver=saver)

    def paid() -> str:
        calls.append(1)
        return "answer"

    for _ in range(2):
        with pytest.raises(BudgetExceededError), agent.session(thread_id="t") as s:
            s.run_turn(paid, model=MODEL, usage=ONE_DOLLAR, idempotency_key="k")

    assert len(calls) == 1


def test_governance_sees_cost_of_turns_rejected_by_the_budget() -> None:
    agent = _agent(
        budget_usd=0.5,
        enforce_budget=True,
        governance=GovernancePlane(limits=(MaxBudgetLimit(value=1.5),)),
    )
    with agent.session() as s:
        with pytest.raises(BudgetExceededError):
            s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
        assert s.governance is not None
        assert s.governance.state.cost_usd == pytest.approx(1.0)
        s.complete()


def test_failing_after_model_hook_still_books_the_paid_turn() -> None:
    class Boom:
        def after_model(self, ctx: object, result: object) -> object:
            raise RuntimeError("hook failed")

    agent = _agent(
        hooks=[Boom()],
        governance=GovernancePlane(limits=(MaxConsecutiveFailuresLimit(value=1),)),
    )
    with agent.session() as s:
        for _ in range(2):
            with pytest.raises(RuntimeError):
                s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
        assert s.tracker.total_cost() == pytest.approx(2.0)
        assert s.governance is not None
        assert s.governance.state.cost_usd == pytest.approx(2.0)
        with pytest.raises(GovernanceBreachError):
            s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
        s.complete()


def test_usage_above_bucket_capacity_does_not_lose_the_paid_turn() -> None:
    saver = InMemorySaver()
    calls: list[int] = []
    limiter = RateLimiter(buckets={"input_tpm": TokenBucket("i", 100, 100_000)})
    agent = _agent(saver=saver, rate_limiter=limiter)

    def paid() -> str:
        calls.append(1)
        return "answer"

    with agent.session(thread_id="t") as s:
        for _ in range(2):
            result, _usage = s.run_turn(
                paid, model=MODEL, usage=ONE_DOLLAR, idempotency_key="k"
            )
            assert result == "answer"
    assert len(calls) == 1


def test_usage_without_a_model_counts_towards_token_limits() -> None:
    agent = _agent(usage_limits=UsageLimits(total_tokens_max=1))
    with pytest.raises(UsageLimitExceededError), agent.session() as s:
        s.run_turn(lambda: "x", usage=UsageSnapshot(input_tokens=10, output_tokens=10))


def test_iteration_cap_is_checked_before_a_rate_limit_token_is_spent() -> None:
    bucket = TokenBucket("rpm", 5, 5)
    agent = _agent(max_iterations=0, rate_limiter=RateLimiter(buckets={"rpm": bucket}))
    with pytest.raises(MaxIterationsExceededError), agent.session() as s:
        s.run_turn(lambda: "x", model=MODEL)
    assert bucket.available == pytest.approx(5, abs=0.5)


# -- replay -------------------------------------------------------------------


def test_replay_restores_spend_so_a_restart_cannot_reset_the_budget() -> None:
    saver = InMemorySaver()
    agent = _agent(budget_usd=1.5, enforce_budget=True, saver=saver)
    with agent.session(thread_id="t") as s:
        s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR, idempotency_key="1")

    with pytest.raises(BudgetExceededError), agent.session(thread_id="t") as s:
        s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR, idempotency_key="1")
        assert s.tracker.total_cost() == pytest.approx(1.0)
        s.run_turn(lambda: "y", model=MODEL, usage=ONE_DOLLAR, idempotency_key="2")


def test_replay_does_not_feed_usage_sinks_twice() -> None:
    records: list[str] = []

    class Sink:
        def record(self, model: str, usage: object, cost: float) -> None:
            records.append(model)

    saver = InMemorySaver()
    agent = _agent(saver=saver, usage_sink=Sink())
    for _ in range(2):
        with agent.session(thread_id="t") as s:
            s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR, idempotency_key="1")
    assert records == [MODEL]


# -- streaming ----------------------------------------------------------------


async def _chunks() -> AsyncIterator[str]:
    for chunk in ("a", "b", "c"):
        yield chunk
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stream_timeout_never_cancels_the_consumer() -> None:
    agent = _agent()
    seen: list[str] = []
    with pytest.raises(TurnTimeoutError):
        async with agent.asession() as s:
            stream = s.arun_turn_stream(_chunks, timeout=0.01)
            async with contextlib.aclosing(stream) as events:
                async for event in events:
                    seen.append(event.type)
                    await asyncio.sleep(0.03)
    assert seen == ["text_delta", "error", "final"]
    current = asyncio.current_task()
    assert current is not None and current.cancelling() == 0


@pytest.mark.asyncio
async def test_abandoned_stream_timer_cannot_fire_into_later_code() -> None:
    agent = _agent()
    async with agent.asession() as s:
        stream = s.arun_turn_stream(_chunks, timeout=0.01)
        async for _event in stream:
            break
        await asyncio.sleep(0.03)  # must not raise CancelledError
        await stream.aclose()


@pytest.mark.asyncio
async def test_upstream_timeout_error_is_not_reported_as_our_deadline() -> None:
    async def flaky() -> AsyncIterator[str]:
        yield "a"
        raise TimeoutError("socket")

    agent = _agent()
    with pytest.raises(TimeoutError) as caught:
        async with agent.asession() as s:
            async for _event in s.arun_turn_stream(flaky):
                pass
    assert not isinstance(caught.value, TurnTimeoutError)


@pytest.mark.asyncio
async def test_limit_crossed_after_stream_ends_still_yields_a_terminal_event() -> None:
    agent = _agent(budget_usd=0.5, enforce_budget=True)
    seen: list[str] = []
    with pytest.raises(BudgetExceededError):
        async with agent.asession() as s:
            async for event in s.arun_turn_stream(
                _chunks, model=MODEL, usage=ONE_DOLLAR
            ):
                seen.append(event.type)
    assert seen[-2:] == ["error", "final"]


@pytest.mark.asyncio
async def test_breaking_out_of_a_stream_books_estimated_usage() -> None:
    agent = _agent()
    async with agent.asession() as s:
        stream = s.arun_turn_stream(
            _chunks, model=MODEL, estimate_usage=lambda text: ONE_DOLLAR
        )
        async with contextlib.aclosing(stream) as events:
            async for _event in events:
                break
        assert s.tracker.total_cost() == pytest.approx(1.0)
        assert s._last_stream_cancelled


@pytest.mark.asyncio
async def test_breach_while_cancelling_does_not_replace_cancellation() -> None:
    agent = _agent(
        governance=GovernancePlane(limits=(MaxConsecutiveFailuresLimit(value=0),)),
    )
    started = asyncio.Event()

    async def hangs() -> AsyncIterator[str]:
        started.set()
        await asyncio.sleep(10)
        yield "never"

    async def run() -> None:
        async with agent.asession() as s:
            async for _event in s.arun_turn_stream(hangs):
                pass

    task = asyncio.create_task(run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_stream_replays_a_recorded_turn_without_calling_upstream() -> None:
    saver = InMemorySaver()
    agent = _agent(saver=saver)
    pulled: list[int] = []

    async def counted() -> AsyncIterator[str]:
        pulled.append(1)
        yield "hello"

    outputs: list[list[str]] = []
    for _ in range(2):
        async with agent.asession(thread_id="t") as s:
            events = [
                event
                async for event in s.arun_turn_stream(
                    counted, model=MODEL, usage=ONE_DOLLAR, idempotency_key="k"
                )
            ]
            outputs.append([e.type for e in events])
            assert s.tracker.total_cost() == pytest.approx(1.0)
    assert pulled == [1]
    assert outputs[0] == outputs[1] == ["text_delta", "final"]


# -- timeouts -----------------------------------------------------------------


def test_timeout_keeps_context_variables_in_the_worker_thread() -> None:
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker")
    marker.set("span-1")
    with _agent().session() as s:
        result, _ = s.run_turn(lambda: marker.get("missing"), timeout=5.0)
    assert result == "span-1"


def test_timeout_error_raised_by_fn_is_not_reported_as_the_deadline() -> None:
    def fails() -> str:
        raise TimeoutError("socket")

    with pytest.raises(TimeoutError) as caught, _agent().session() as s:
        s.run_turn(fails, timeout=5.0)
    assert not isinstance(caught.value, TurnTimeoutError)


@pytest.mark.asyncio
async def test_async_timeout_error_raised_by_fn_is_not_the_deadline() -> None:
    async def fails() -> str:
        raise TimeoutError("socket")

    with pytest.raises(TimeoutError) as caught:
        async with _agent().asession() as s:
            await s.arun_turn(fails, timeout=5.0)
    assert not isinstance(caught.value, TurnTimeoutError)


@pytest.mark.asyncio
async def test_timed_out_approval_wait_leaves_the_session_usable() -> None:
    agent = _agent()
    async with agent.asession() as s:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(s.pause_for_input("approve?"), 0.01)
        assert s.worker.status == AgentStatus.RUNNING
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(s.pause_for_input("approve?"), 0.01)
    assert s.worker.status == AgentStatus.COMPLETED


# -- lifecycle ----------------------------------------------------------------


def test_exception_after_handoff_does_not_claim_both_outcomes() -> None:
    agent = _agent()
    with pytest.raises(RuntimeError), agent.session() as s:
        s.handoff_to("b", "reason")
        raise RuntimeError("late")
    assert not [e for e in s.events if e.event.value == "agent.failed"]


def test_completing_twice_emits_one_completion() -> None:
    with _agent().session() as s:
        s.complete()
        s.complete()
    assert len([e for e in s.events if e.event.value == "agent.completed"]) == 1


def test_target_session_adopts_the_worker_reserved_by_a_handoff() -> None:
    source = _agent()
    with source.session() as s:
        handoff = s.handoff_to("b", "reason")
    target = AgentSession(role="b", phase="p", registry=source.registry)
    with target.session(handoff=handoff) as t:
        assert t.worker.worker_id == handoff.target_worker_id
    assert source.registry.list_active() == []


def test_handoff_cannot_be_adopted_twice_or_by_another_role() -> None:
    source = _agent()
    with source.session() as s:
        handoff = s.handoff_to("b", "reason")
    other = AgentSession(role="c", phase="p", registry=source.registry)
    with (
        pytest.raises(ValueError, match="targets role"),
        other.session(handoff=handoff),
    ):
        pass
    target = AgentSession(role="b", phase="p", registry=source.registry)
    with target.session(handoff=handoff):
        pass
    with (
        pytest.raises(ValueError, match="no pending worker"),
        target.session(handoff=handoff),
    ):
        pass


@pytest.mark.parametrize(
    "exc",
    [
        TurnTimeoutError(1.5),
        ToolTimeoutError("search", 1.5),
        MaxIterationsExceededError(3),
        PermissionDeniedError(PermissionOutcome(allowed=False, reason="no")),
    ],
)
def test_session_exceptions_survive_pickling(exc: Exception) -> None:
    clone = copy.copy(exc)
    assert type(clone) is type(exc)
    assert str(clone) == str(exc)


# -- turn events --------------------------------------------------------------


def test_turn_events_carry_duration_usage_and_failure_type() -> None:
    def fails() -> str:
        raise ValueError("bad")

    with _agent().session() as s:
        s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
        with pytest.raises(ValueError):
            s.run_turn(fails, model=MODEL)
        names = [e.event.value for e in s.events]
        completed = next(e for e in s.events if e.event.value == "agent.turn.completed")
        failed = next(e for e in s.events if e.event.value == "agent.turn.failed")
    assert "agent.turn.completed" in names
    assert completed.data["model"] == MODEL
    assert completed.data["duration_seconds"] >= 0
    assert completed.data["input_tokens"] == 1000
    assert completed.data["cost_usd"] == pytest.approx(1.0)
    assert failed.data["error_type"] == "ValueError"


# -- tool features ------------------------------------------------------------


def test_tool_timeout_raises_a_typed_error_and_records_duration() -> None:
    import threading

    release = threading.Event()
    with _agent().session() as s:
        with pytest.raises(ToolTimeoutError) as caught:
            s.run_tool("slow", release.wait, timeout=0.01)
        release.set()
        failed = [e for e in s.events if e.event.value == "agent.failed"][-1]
    assert caught.value.tool_name == "slow"
    assert failed.data["tool"] == "slow"
    assert failed.data["error_type"] == "ToolTimeoutError"


@pytest.mark.asyncio
async def test_async_tool_timeout_cancels_the_tool() -> None:
    cancelled: list[bool] = []

    async def slow() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async with _agent().asession() as s:
        with pytest.raises(ToolTimeoutError):
            await s.arun_tool("slow", slow, timeout=0.01)
    assert cancelled == [True]


def test_tool_idempotency_key_replays_instead_of_repeating_the_side_effect() -> None:
    saver = InMemorySaver()
    calls: list[int] = []
    agent = _agent(saver=saver)

    def charge() -> dict[str, str]:
        calls.append(1)
        return {"charge": "ch_1"}

    results = []
    for _ in range(2):
        with agent.session(thread_id="t") as s:
            results.append(s.run_tool("charge", charge, idempotency_key="order-7"))
    assert len(calls) == 1
    assert results == [{"charge": "ch_1"}, {"charge": "ch_1"}]


def test_tool_replay_keeps_a_recorded_none_result() -> None:
    saver = InMemorySaver()
    calls: list[int] = []
    agent = _agent(saver=saver)

    def send() -> None:
        calls.append(1)

    for _ in range(2):
        with agent.session(thread_id="t") as s:
            assert s.run_tool("send", send, idempotency_key="k") is None
    assert len(calls) == 1


def test_tool_replay_still_honours_a_revoked_permission() -> None:
    from techrevati.runtime.permissions import (
        PermissionEnforcer,
        PermissionMode,
        PermissionPolicy,
        RolePermissionConfig,
    )

    saver = InMemorySaver()
    with _agent(saver=saver).session(thread_id="t") as s:
        s.run_tool("charge", lambda: 1, idempotency_key="k")

    policy = PermissionPolicy(
        {
            "r": RolePermissionConfig(
                "r", PermissionMode.READ_ONLY, denied_tools=["charge"]
            )
        },
        {"charge": PermissionMode.FULL_ACCESS},
    )
    revoked = _agent(saver=saver, permissions=PermissionEnforcer(policy))
    with pytest.raises(PermissionDeniedError), revoked.session(thread_id="t") as s:
        s.run_tool("charge", lambda: 1, idempotency_key="k")


def test_failed_tool_is_not_recorded_for_replay() -> None:
    saver = InMemorySaver()
    attempts: list[int] = []

    def flaky() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return "ok"

    with _agent(saver=saver).session(thread_id="t") as s:
        with pytest.raises(RuntimeError):
            s.run_tool("t", flaky, idempotency_key="k")
        assert s.run_tool("t", flaky, idempotency_key="k") == "ok"


# -- loop detection -----------------------------------------------------------


def test_repeated_identical_tool_call_is_stopped() -> None:
    plane = GovernancePlane(limits=(MaxRepeatedToolCallsLimit(value=3),))
    with pytest.raises(GovernanceBreachError) as caught:
        with _agent(governance=plane).session() as s:
            for _ in range(4):
                s.run_tool("search", lambda: 1, args={"q": "x", "n": 1})
    assert caught.value.limit_name == "max_repeated_tool_calls"
    assert caught.value.observed == 4


def test_key_order_cannot_hide_a_repeat() -> None:
    assert tool_call_signature("t", {"a": 1, "b": 2}) == tool_call_signature(
        "t", {"b": 2, "a": 1}
    )


def test_different_arguments_or_tools_are_not_a_loop() -> None:
    plane = GovernancePlane(limits=(MaxRepeatedToolCallsLimit(value=1),))
    with _agent(governance=plane).session() as s:
        for i in range(5):
            s.run_tool("search", lambda: 1, args={"q": i})
        s.run_tool("fetch", lambda: 1, args={"q": 4})


def test_calls_without_args_are_never_treated_as_a_loop() -> None:
    plane = GovernancePlane(limits=(MaxRepeatedToolCallsLimit(value=1),))
    with _agent(governance=plane).session() as s:
        for _ in range(5):
            s.run_tool("poll", lambda: 1)


def test_repeat_outside_the_window_is_forgotten() -> None:
    plane = GovernancePlane(limits=(MaxRepeatedToolCallsLimit(value=1, window=2),))
    with _agent(governance=plane).session() as s:
        s.run_tool("a", lambda: 1, args={"x": 1})
        s.run_tool("b", lambda: 1, args={"x": 1})
        s.run_tool("c", lambda: 1, args={"x": 1})
        s.run_tool("a", lambda: 1, args={"x": 1})


def test_oscillating_calls_are_caught_inside_the_window() -> None:
    plane = GovernancePlane(limits=(MaxRepeatedToolCallsLimit(value=2, window=10),))
    with pytest.raises(GovernanceBreachError):
        with _agent(governance=plane).session() as s:
            for _ in range(3):
                s.run_tool("a", lambda: 1, args={"x": 1})
                s.run_tool("b", lambda: 1, args={"x": 1})


def test_unencodable_args_still_produce_a_signature() -> None:
    circular: dict[str, object] = {}
    circular["self"] = circular
    assert tool_call_signature("t", circular)
    assert tool_call_signature("t", {1: "a", "b": 2})


@pytest.mark.parametrize("window", [0, -1, 1025, True, 1.5])
def test_invalid_window_is_rejected(window: object) -> None:
    with pytest.raises(ValueError):
        MaxRepeatedToolCallsLimit(value=3, window=window)  # type: ignore[arg-type]


# -- provider fallback --------------------------------------------------------


def _provider_error() -> Exception:
    return RuntimeError("503 service unavailable")


def test_fallback_moves_to_the_next_model_and_reports_which_answered() -> None:
    called: list[str] = []

    def call(model: str) -> str:
        called.append(model)
        if model == "a":
            raise _provider_error()
        return f"from {model}"

    agent = _agent(provider_router=StaticProviderRouter(("a", "b", "c")))
    with agent.session() as s:
        result, _usage, used = s.run_turn_with_fallback(call, model="a")
    assert (result, used, called) == ("from b", "b", ["a", "b"])


def test_fallback_gives_up_when_every_provider_failed() -> None:
    called: list[str] = []

    def call(model: str) -> str:
        called.append(model)
        raise _provider_error()

    agent = _agent(provider_router=StaticProviderRouter(("a", "b")))
    with pytest.raises(RuntimeError), agent.session() as s:
        s.run_turn_with_fallback(call, model="a")
    assert called == ["a", "b"]


def test_fallback_ignores_failures_another_provider_would_not_fix() -> None:
    called: list[str] = []

    def call(model: str) -> str:
        called.append(model)
        raise RuntimeError("maximum context length exceeded")

    agent = _agent(provider_router=StaticProviderRouter(("a", "b")))
    with pytest.raises(RuntimeError), agent.session() as s:
        s.run_turn_with_fallback(call, model="a")
    assert called == ["a"]


def test_fallback_is_bounded_by_the_iteration_cap() -> None:
    called: list[str] = []

    def call(model: str) -> str:
        called.append(model)
        raise _provider_error()

    agent = _agent(
        provider_router=StaticProviderRouter(("a", "b", "c")), max_iterations=2
    )
    with pytest.raises(MaxIterationsExceededError), agent.session() as s:
        s.run_turn_with_fallback(call, model="a")
    assert called == ["a", "b"]


def test_fallback_requires_a_router() -> None:
    with _agent().session() as s:
        with pytest.raises(ValueError, match="provider_router"):
            s.run_turn_with_fallback(lambda model: model, model="a")


def test_fallback_reports_the_recorded_model_on_replay() -> None:
    saver = InMemorySaver()
    agent = _agent(saver=saver, provider_router=StaticProviderRouter(("a", "b")))

    def call(model: str) -> str:
        if model == "a":
            raise _provider_error()
        return model

    with agent.session(thread_id="t") as s:
        s.run_turn_with_fallback(call, model="a", idempotency_key="k")
    with agent.session(thread_id="t") as s:
        _, _, used = s.run_turn_with_fallback(call, model="a", idempotency_key="k")
    assert used == "b"


@pytest.mark.asyncio
async def test_async_fallback_moves_to_the_next_model() -> None:
    async def call(model: str) -> str:
        if model == "a":
            raise _provider_error()
        return model

    agent = _agent(provider_router=StaticProviderRouter(("a", "b")))
    async with agent.asession() as s:
        result, _usage, used = await s.arun_turn_with_fallback(call, model="a")
    assert (result, used) == ("b", "b")


# -- adversarial review of the above ------------------------------------------


@pytest.mark.asyncio
async def test_budget_stops_the_next_turn_after_a_stream_failed_early() -> None:
    async def fails_midway() -> AsyncIterator[str]:
        yield "a"
        raise RuntimeError("boom")

    pulled: list[int] = []

    async def counted() -> AsyncIterator[str]:
        pulled.append(1)
        yield "a"

    agent = _agent(budget_usd=0.5, enforce_budget=True)
    with pytest.raises(BudgetExceededError):
        async with agent.asession() as s:
            with pytest.raises(RuntimeError):
                async for _event in s.arun_turn_stream(
                    fails_midway, model=MODEL, usage=ONE_DOLLAR
                ):
                    pass
            async for _event in s.arun_turn_stream(counted, model=MODEL):
                pass
    assert pulled == []


def test_budget_stops_the_next_turn_after_a_failing_after_model_hook() -> None:
    class Boom:
        def after_model(self, ctx: object, result: object) -> object:
            raise RuntimeError("hook failed")

    calls: list[int] = []

    def paid() -> str:
        calls.append(1)
        return "x"

    agent = _agent(budget_usd=0.5, enforce_budget=True, hooks=[Boom()])
    with pytest.raises(BudgetExceededError), agent.session() as s:
        with pytest.raises(RuntimeError):
            s.run_turn(paid, model=MODEL, usage=ONE_DOLLAR)
        s.run_turn(paid, model=MODEL, usage=ONE_DOLLAR)
    assert calls == [1]


def test_unenforced_budget_does_not_block_or_double_report() -> None:
    agent = _agent(budget_usd=0.5, enforce_budget=False)
    with agent.session() as s:
        s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
        s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
        over = [e for e in s.events if "budget exceeded" in (e.detail or "")]
    assert len(over) == 2


class _BrokenSaver(InMemorySaver):
    def put(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def,override]
        raise ConnectionError("saver down")


def test_saver_outage_does_not_hide_spend_from_governance() -> None:
    agent = _agent(
        saver=_BrokenSaver(),
        governance=GovernancePlane(limits=(MaxBudgetLimit(value=1.5),)),
    )
    with agent.session(thread_id="t") as s:
        for _ in range(2):
            with pytest.raises(ConnectionError):
                s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
        assert s.governance is not None
        assert s.governance.state.cost_usd == pytest.approx(2.0)
        assert any(e.event.value == "agent.turn.completed" for e in s.events)
        with pytest.raises(GovernanceBreachError):
            s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
        s.complete()


@pytest.mark.asyncio
async def test_saver_outage_still_ends_a_stream_with_terminal_events() -> None:
    agent = _agent(saver=_BrokenSaver())
    seen: list[str] = []
    with pytest.raises(ConnectionError):
        async with agent.asession(thread_id="t") as s:
            async for event in s.arun_turn_stream(_chunks, model=MODEL):
                seen.append(event.type)
    assert seen[-2:] == ["error", "final"]


def test_fallback_does_not_pay_again_when_a_hook_fails_after_success() -> None:
    class Boom:
        def after_model(self, ctx: object, result: object) -> object:
            raise TimeoutError("looks like a provider timeout")

    called: list[str] = []

    def call(model: str) -> str:
        called.append(model)
        return model

    agent = _agent(hooks=[Boom()], provider_router=StaticProviderRouter(("a", "b")))
    with pytest.raises(TimeoutError), agent.session() as s:
        s.run_turn_with_fallback(call, model="a")
    assert called == ["a"]


def test_fallback_does_not_pay_again_when_the_saver_fails_after_success() -> None:
    called: list[str] = []

    def call(model: str) -> str:
        called.append(model)
        return model

    agent = _agent(
        saver=_BrokenSaver(), provider_router=StaticProviderRouter(("a", "b"))
    )
    with pytest.raises(ConnectionError), agent.session(thread_id="t") as s:
        s.run_turn_with_fallback(call, model="a")
    assert called == ["a"]


def test_retrying_a_key_in_one_session_counts_it_once() -> None:
    saver = InMemorySaver()
    agent = _agent(saver=saver, budget_usd=1.5, enforce_budget=True)
    with agent.session(thread_id="t") as s:
        for _ in range(3):
            s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR, idempotency_key="k")
        assert s.tracker.total_cost() == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_cancelled_after_model_hook_still_books_the_paid_turn() -> None:
    entered = asyncio.Event()

    class Slow:
        async def after_model(self, ctx: object, result: object) -> object:
            entered.set()
            await asyncio.sleep(10)
            return result

    agent = _agent(hooks=[Slow()])
    sessions = []

    async def call() -> str:
        return "x"

    async def run() -> None:
        async with agent.asession() as s:
            sessions.append(s)
            await s.arun_turn(call, model=MODEL, usage=ONE_DOLLAR)

    task = asyncio.create_task(run())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sessions[0].tracker.total_cost() == pytest.approx(1.0)


def test_reused_hook_context_shows_the_current_calls_args() -> None:
    from techrevati.runtime import HookContext

    seen: list[object] = []

    class Spy:
        def before_tool(self, ctx: HookContext) -> None:
            seen.append(dict(ctx.args))

    ctx = HookContext(role="r", phase="p")
    with _agent(hooks=[Spy()]).session() as s:
        s.run_tool("read", lambda: 1, args={"path": "/safe"}, hook_ctx=ctx)
        s.run_tool("read", lambda: 1, args={"path": "/etc/shadow"}, hook_ctx=ctx)
    assert seen == [{"path": "/safe"}, {"path": "/etc/shadow"}]


@pytest.mark.asyncio
async def test_non_string_chunk_gives_terminal_events_and_books_spend() -> None:
    async def bad() -> AsyncIterator[str]:
        yield "hello"
        yield None  # type: ignore[misc]

    agent = _agent()
    seen: list[str] = []
    with pytest.raises(TypeError, match="must be str"):
        async with agent.asession() as s:
            try:
                async for event in s.arun_turn_stream(
                    bad, model=MODEL, usage=ONE_DOLLAR
                ):
                    seen.append(event.type)
            finally:
                assert s.tracker.total_cost() == pytest.approx(1.0)
    assert seen == ["text_delta", "error", "final"]


@pytest.mark.asyncio
async def test_leaving_a_stream_on_purpose_is_not_a_failure() -> None:
    agent = _agent(
        governance=GovernancePlane(limits=(MaxConsecutiveFailuresLimit(value=1),)),
    )
    async with agent.asession() as s:
        for _ in range(3):
            stream = s.arun_turn_stream(_chunks, model=MODEL)
            async with contextlib.aclosing(stream) as events:
                async for _event in events:
                    break
        assert s.governance is not None
        assert s.governance.state.consecutive_failures == 0
        failed = [e for e in s.events if e.event.value == "agent.turn.failed"]
    assert {e.data["error_type"] for e in failed} == {"cancelled"}


@pytest.mark.asyncio
async def test_stream_failing_before_any_chunk_charges_nothing() -> None:
    async def refused() -> AsyncIterator[str]:
        raise ConnectionError("refused")
        yield "never"

    agent = _agent()
    async with agent.asession() as s:
        with pytest.raises(ConnectionError):
            async for _event in s.arun_turn_stream(
                refused, model=MODEL, usage=ONE_DOLLAR
            ):
                pass
        assert s.tracker.total_cost() == 0


def test_budget_limit_ignores_float_noise_but_not_real_overruns() -> None:
    from techrevati.runtime.governance import GovernanceState

    state = GovernanceState()
    for _ in range(3):
        state.record_cost(0.1)
    within = GovernancePlane(limits=(MaxBudgetLimit(value=0.3),), state=state)
    assert within.enforce()[0].breached is False
    state.record_cost(0.01)
    with pytest.raises(GovernanceBreachError):
        within.enforce()


def test_step_keys_of_different_tools_cannot_collide() -> None:
    saver = InMemorySaver()
    with _agent(saver=saver).session(thread_id="t") as s:
        first = s.run_tool("a", lambda: "A", idempotency_key="b:c")
        second = s.run_tool("a:b", lambda: "AB", idempotency_key="c")
    assert (first, second) == ("A", "AB")


def test_fallback_runs_hooks_on_the_original_prompt_each_attempt() -> None:
    from techrevati.runtime import HookContext

    class Prefix:
        def before_model(self, ctx: HookContext) -> None:
            ctx.prompt = f"SAFE: {ctx.prompt}"

    prompts: list[object] = []
    ctx = HookContext(role="r", phase="p", prompt="hi")

    def call(model: str) -> str:
        prompts.append(ctx.prompt)
        if model == "a":
            raise _provider_error()
        return model

    agent = _agent(hooks=[Prefix()], provider_router=StaticProviderRouter(("a", "b")))
    with agent.session() as s:
        s.run_turn_with_fallback(call, model="a", hook_ctx=ctx)
    assert prompts == ["SAFE: hi", "SAFE: hi"]


def test_repeat_alert_fires_per_call_not_on_every_later_turn() -> None:
    plane = GovernancePlane(
        limits=(MaxRepeatedToolCallsLimit(value=1, on_breach="alert"),)
    )
    with _agent(governance=plane).session() as s:
        for _ in range(2):
            s.run_tool("t", lambda: 1, args={"x": 1})
        for _ in range(5):
            s.run_turn(lambda: "x")
        alerts = [e for e in s.events if e.event.value == "governance.alert"]
    assert len(alerts) == 1


def test_breach_in_terminate_mode_stays_visible_to_later_checks() -> None:
    plane = GovernancePlane(limits=(MaxRepeatedToolCallsLimit(value=1),))
    with _agent(governance=plane).session() as s:
        s.run_tool("t", lambda: 1, args={"x": 1})
        with pytest.raises(GovernanceBreachError):
            s.run_tool("t", lambda: 1, args={"x": 1})
        with pytest.raises(GovernanceBreachError):
            s.run_turn(lambda: "x")
        s.complete()


def test_unrepresentable_args_do_not_fail_the_tool() -> None:
    class Hostile:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    plane = GovernancePlane(limits=(MaxRepeatedToolCallsLimit(value=1),))
    with _agent(governance=plane).session() as s:
        assert s.run_tool("t", lambda: 1, args={"x": Hostile()}) == 1


@pytest.mark.asyncio
async def test_hanging_upstream_close_is_bounded_by_a_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from techrevati.runtime import orchestrator

    monkeypatch.setattr(orchestrator, "_STREAM_CLOSE_GRACE_SECONDS", 0.01)

    class Stuck:
        def __aiter__(self) -> Stuck:
            return self

        async def __anext__(self) -> str:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            await asyncio.sleep(10)

    async with _agent().asession() as s:
        async with asyncio.timeout(2):
            async for _event in s.arun_turn_stream(Stuck, timeout=1.0):
                pass


@pytest.mark.asyncio
async def test_stream_replay_of_none_and_over_budget() -> None:
    saver = InMemorySaver()
    agent = _agent(saver=saver, budget_usd=0.5, enforce_budget=True)
    async with agent.asession(thread_id="t") as s:
        with pytest.raises(BudgetExceededError):
            await s.arun_turn(
                _none_result, model=MODEL, usage=ONE_DOLLAR, idempotency_key="k"
            )
    seen: list[str] = []
    with pytest.raises(BudgetExceededError):
        async with agent.asession(thread_id="t") as s:
            async for event in s.arun_turn_stream(
                _chunks, model=MODEL, idempotency_key="k"
            ):
                seen.append(event.type)
    assert seen == ["error", "final"]


async def _none_result() -> None:
    return None


def test_unstorable_thread_id_is_rejected_before_any_turn_is_paid() -> None:
    agent = _agent(saver=InMemorySaver())
    with pytest.raises(ValueError, match="valid Unicode"):
        with agent.session(thread_id="bad\ud800"):
            pass


# -- maintainer decisions for 0.5.0 -------------------------------------------


def test_unknown_role_is_denied_by_default() -> None:
    from techrevati.runtime.permissions import PermissionPolicy

    assert PermissionPolicy().authorize("nobody", "tool").allowed is False
    allowed = PermissionPolicy(default_allow_unknown_roles=True)
    assert allowed.authorize("nobody", "tool").allowed is True


def test_sync_timeout_counts_as_a_breaker_failure() -> None:
    import threading

    from techrevati.runtime import CircuitBreaker, CircuitOpenError

    release = threading.Event()
    breaker = CircuitBreaker("svc", failure_threshold=1, recovery_timeout_seconds=60)
    with _agent(circuit_breaker=breaker).session() as s:
        with pytest.raises(TurnTimeoutError):
            s.run_turn(lambda: release.wait(5), timeout=0.01)
        with pytest.raises(CircuitOpenError):
            s.run_turn(lambda: "x")
        s.complete()
    release.set()


@pytest.mark.asyncio
async def test_async_timeout_counts_as_a_breaker_failure() -> None:
    from techrevati.runtime import AsyncCircuitBreaker, CircuitOpenError

    async def hangs() -> str:
        await asyncio.sleep(10)
        return "x"

    breaker = AsyncCircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=60
    )
    async with _agent(async_circuit_breaker=breaker).asession() as s:
        with pytest.raises(TurnTimeoutError):
            await s.arun_turn(hangs, timeout=0.01)
        with pytest.raises(CircuitOpenError):
            await s.arun_turn(hangs)
        s.complete()


def test_hung_probe_gives_its_permit_back_at_the_deadline() -> None:
    import threading

    from techrevati.runtime import CircuitBreaker, ManualClock

    clock = ManualClock()
    release = threading.Event()
    breaker = CircuitBreaker(
        "svc", failure_threshold=1, recovery_timeout_seconds=10, clock=clock.monotonic
    )
    breaker.record_failure()
    clock.advance(11)
    with _agent(circuit_breaker=breaker).session() as s:
        with pytest.raises(TurnTimeoutError):
            s.run_turn(lambda: release.wait(5), timeout=0.01)
        clock.advance(11)
        assert s.run_turn(lambda: "ok")[0] == "ok"
    release.set()


class _FailingAudit:
    """An audit log whose writes fail once ``broken`` is set."""

    def __new__(cls, broken: bool = False):  # type: ignore[no-untyped-def]
        from techrevati.runtime.compliance import AuditLogSink, InMemoryAuditBackend

        class Sink(AuditLogSink):
            is_broken = broken

            def emit(self, event: object) -> None:  # type: ignore[override]
                if self.is_broken:
                    raise OSError("disk full")
                super().emit(event)  # type: ignore[arg-type]

        return Sink(InMemoryAuditBackend())


def test_fail_closed_needs_an_audit_log() -> None:
    with pytest.raises(ValueError, match="audit_fail_closed requires"):
        _agent(audit_fail_closed=True)


def test_broken_log_blocks_the_first_action_when_failing_closed() -> None:
    from techrevati.runtime import AuditWriteError

    ran: list[int] = []
    agent = _agent(audit_log=_FailingAudit(broken=True), audit_fail_closed=True)
    with pytest.raises(AuditWriteError), agent.session() as s:
        s.run_tool("t", lambda: ran.append(1))
    assert ran == []


def test_failed_write_stops_the_tool_it_should_have_recorded() -> None:
    from techrevati.runtime import AuditWriteError

    log = _FailingAudit()
    ran: list[int] = []

    class BreakLog:
        def before_tool(self, ctx: object) -> None:
            log.is_broken = True

    agent = _agent(audit_log=log, audit_fail_closed=True, hooks=[BreakLog()])
    with pytest.raises(AuditWriteError), agent.session() as s:
        s.run_tool("t", lambda: ran.append(1))
    assert ran == []


def test_failure_on_turn_record_raises_after_booking_and_blocks_later_actions() -> None:
    from techrevati.runtime import AuditWriteError

    log = _FailingAudit()
    calls: list[int] = []

    def breaks_log() -> str:
        calls.append(1)
        log.is_broken = True
        return "x"

    agent = _agent(audit_log=log, audit_fail_closed=True)
    with agent.session() as s:
        with pytest.raises(AuditWriteError):
            s.run_turn(breaks_log, model=MODEL, usage=ONE_DOLLAR)
        assert s.tracker.total_cost() == pytest.approx(1.0)
        with pytest.raises(AuditWriteError):
            s.run_turn(breaks_log, model=MODEL, usage=ONE_DOLLAR)
        with pytest.raises(AuditWriteError):
            s.run_tool("t", lambda: 1)
        log.is_broken = False
    assert calls == [1]


def test_compliance_kit_log_is_guarded_too() -> None:
    from techrevati.runtime import AuditWriteError
    from techrevati.runtime.compliance import EUAIActComplianceKit

    kit = EUAIActComplianceKit.standard(audit_log=_FailingAudit(broken=True))
    agent = _agent(compliance=kit, audit_fail_closed=True)
    with pytest.raises(AuditWriteError), agent.session() as s:
        s.run_tool("t", lambda: 1)


def test_broken_log_keeps_the_session_running_by_default() -> None:
    agent = _agent(audit_log=_FailingAudit(broken=True))
    with agent.session() as s:
        assert s.run_tool("t", lambda: 1) == 1


def test_broken_caller_sink_does_not_trigger_fail_closed() -> None:
    from techrevati.runtime.compliance import AuditLogSink, InMemoryAuditBackend

    class Broken:
        def emit(self, event: object) -> None:
            raise OSError("metrics down")

    agent = _agent(
        event_sink=Broken(),
        audit_log=AuditLogSink(InMemoryAuditBackend()),
        audit_fail_closed=True,
    )
    with agent.session() as s:
        assert s.run_tool("t", lambda: 1) == 1


def test_audit_write_error_survives_copying() -> None:
    from techrevati.runtime import AuditWriteError

    clone = copy.copy(AuditWriteError("OSError"))
    assert clone.error_type == "OSError"
    assert "OSError" in str(clone)

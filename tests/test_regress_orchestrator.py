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
        with pytest.raises(GovernanceBreachError):
            s.run_turn(lambda: "x", model=MODEL, usage=ONE_DOLLAR)
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
    limiter = RateLimiter(buckets={"input_tpm": TokenBucket("i", 100, 100)})
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

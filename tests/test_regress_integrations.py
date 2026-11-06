"""Regressions in memory, typed outputs, usage, events, telemetry (executed PoCs)."""

from __future__ import annotations

import copy
import inspect
import json
import subprocess
import sys
import textwrap
import threading

import pytest

from techrevati.runtime import (
    AgentEvent,
    BudgetExceededError,
    ModelPricing,
    UsageLimitExceededError,
    UsageLimits,
    UsageSnapshot,
    UsageTracker,
    has_pricing,
    register_pricing,
    resolve_pricing,
)
from techrevati.runtime.agent_events import (
    AgentEventName,
    AgentEventStatus,
    AgentFailureClass,
)
from techrevati.runtime.agent_lifecycle import (
    AgentRegistry,
    AgentStatus,
    InvalidTransitionError,
)
from techrevati.runtime.memory import (
    InMemoryConversationMemory,
    MemoryMessage,
    TokenBudgetCompaction,
    WindowCompaction,
)
from techrevati.runtime.output_spec import JsonOutputSpec, OutputValidationError
from techrevati.runtime.streaming import StreamEvent
from techrevati.runtime.usage_tracking import (
    PRICING_TABLE,
    PricingAlreadyRegisteredError,
    _pricing_lock,
)


def _roles(messages: list[MemoryMessage]) -> list[str]:
    return [m.role for m in messages]


def _pair_history() -> list[MemoryMessage]:
    return [
        MemoryMessage("system", "s"),
        MemoryMessage("user", "u"),
        MemoryMessage("assistant", "call"),
        MemoryMessage("tool", "result"),
        MemoryMessage("assistant", "done"),
    ]


class TestCompaction:
    @pytest.mark.parametrize(
        "compaction",
        [WindowCompaction(max_messages=3), TokenBudgetCompaction(max_tokens=10)],
    )
    def test_reused_instance_cannot_defeat_the_limit(self, compaction) -> None:
        ack = MemoryMessage("user", "continue")
        memory = InMemoryConversationMemory(compaction=compaction)
        for _ in range(100):
            memory.add(ack)
        assert len(memory) <= 5

    def test_reused_instance_window_is_exact(self) -> None:
        ack = MemoryMessage("user", "continue")
        memory = InMemoryConversationMemory(compaction=WindowCompaction(max_messages=3))
        for _ in range(10):
            memory.add(ack)
        assert len(memory) == 3

    def test_token_budget_keeps_oversized_newest_message(self) -> None:
        history = [
            MemoryMessage("system", "s"),
            MemoryMessage("user", "old"),
            MemoryMessage("assistant", "old"),
            MemoryMessage("user", "x" * 400),
        ]
        kept = TokenBudgetCompaction(max_tokens=20).compact(history)
        assert _roles(kept) == ["system", "user"]
        assert kept[-1].content == "x" * 400

    def test_window_keeps_newest_when_system_fills_it(self) -> None:
        history = [MemoryMessage("system", "s"), MemoryMessage("user", "question")]
        kept = WindowCompaction(max_messages=1).compact(history)
        assert _roles(kept) == ["system", "user"]

    def test_newest_system_message_needs_no_extra_slot(self) -> None:
        history = [
            MemoryMessage("system", "a"),
            MemoryMessage("user", "u"),
            MemoryMessage("system", "b"),
        ]
        assert _roles(WindowCompaction(max_messages=2).compact(history)) == [
            "system",
            "system",
        ]

    @pytest.mark.parametrize(
        "compaction",
        [
            WindowCompaction(max_messages=3),
            TokenBudgetCompaction(max_tokens=3, estimator=lambda _: 1),
        ],
    )
    def test_window_never_starts_on_tool_result(self, compaction) -> None:
        kept = compaction.compact(_pair_history())
        assert _roles(kept) == ["system", "assistant"]

    def test_all_tool_window_pulls_in_the_issuing_message(self) -> None:
        history = [
            MemoryMessage("user", "u"),
            MemoryMessage("assistant", "call"),
            MemoryMessage("tool", "result"),
        ]
        kept = WindowCompaction(max_messages=1, keep_system=False).compact(history)
        assert _roles(kept) == ["assistant", "tool"]

    def test_order_and_system_retention_still_hold(self) -> None:
        kept = WindowCompaction(max_messages=4).compact(_pair_history())
        assert _roles(kept) == ["system", "assistant", "tool", "assistant"]


class TestJsonOutputSpec:
    @pytest.mark.parametrize("raw", ["[" * 200_000, "1" * 5000])
    def test_parser_limits_raise_validation_error(self, raw: str) -> None:
        with pytest.raises(OutputValidationError):
            JsonOutputSpec().parse(raw)

    @pytest.mark.parametrize("raw", ["NaN", '{"score": Infinity}', "-Infinity"])
    def test_non_json_constants_rejected(self, raw: str) -> None:
        with pytest.raises(OutputValidationError):
            JsonOutputSpec().parse(raw)

    def test_bool_is_not_an_int(self) -> None:
        with pytest.raises(OutputValidationError):
            JsonOutputSpec(require_type=int).parse("true")
        assert JsonOutputSpec(require_type=bool).parse("true") is True
        assert JsonOutputSpec(require_type=int).parse("3") == 3

    @pytest.mark.parametrize("separator", [" ", "\x85"])
    def test_fenced_string_keeps_unicode_line_separators(self, separator: str) -> None:
        raw = f'```json\n{{"a":"x{separator}y"}}\n```'
        assert JsonOutputSpec().parse(raw) == {"a": f"x{separator}y"}

    def test_fence_line_with_data_is_not_discarded(self) -> None:
        with pytest.raises(OutputValidationError):
            JsonOutputSpec().parse("```[1,2]\n3\n```")

    @pytest.mark.parametrize(
        "raw",
        ['```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '```json\r\n{"a": 1}\r\n```'],
    )
    def test_normal_fences_still_stripped(self, raw: str) -> None:
        assert JsonOutputSpec().parse(raw) == {"a": 1}


class TestPricing:
    def test_prefix_requires_a_name_boundary(self) -> None:
        register_pricing("boundary-gpt-4", ModelPricing(30.0, 60.0))
        assert not has_pricing("boundary-gpt-4o-mini")
        assert resolve_pricing("boundary-gpt-4o-mini") == ModelPricing(0.0, 0.0)

    @pytest.mark.parametrize("suffix", ["-20260514", ":latest", "@v2", "/eu"])
    def test_real_variants_still_resolve(self, suffix: str) -> None:
        pricing = ModelPricing(3.0, 15.0)
        register_pricing("boundary-model-a", pricing)
        assert has_pricing(f"boundary-model-a{suffix}")
        assert resolve_pricing(f"boundary-model-a{suffix}") == pricing

    def test_longest_variant_prefix_wins(self) -> None:
        register_pricing("boundary-long", ModelPricing(1.0, 1.0))
        register_pricing("boundary-long-pro", ModelPricing(2.0, 2.0))
        assert resolve_pricing("boundary-long-pro-1").input_per_million == 2.0

    def test_has_pricing_survives_concurrent_registration(self) -> None:
        for i in range(2000):
            register_pricing(f"race-seed-{i}", ModelPricing(1.0, 1.0))
        stop = threading.Event()

        def writer() -> None:
            i = 0
            while not stop.is_set():
                register_pricing(f"race-new-{i}", ModelPricing(1.0, 1.0))
                with _pricing_lock:
                    PRICING_TABLE.pop(f"race-new-{i}", None)
                i += 1

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            for _ in range(300):
                assert has_pricing("unregistered-model") is False
        finally:
            stop.set()
            thread.join()
            for i in range(2000):
                PRICING_TABLE.pop(f"race-seed-{i}", None)

    @pytest.mark.parametrize("ttl", [None, "300s"])
    def test_tier_only_pricing_does_not_bill_cache_writes_at_zero(self, ttl) -> None:
        register_pricing(
            "boundary-tiered",
            ModelPricing(
                3.0,
                15.0,
                cache_write_5min_per_million=3.75,
                cache_write_1h_per_million=6.0,
            ),
        )
        usage = UsageSnapshot(cache_write_tokens=1_000_000, cache_ttl=ttl)
        assert UsageTracker().cost_for_turn("boundary-tiered", usage) == 6.0

    def test_ttl_is_case_insensitive(self) -> None:
        pricing = ModelPricing(
            0.0, 0.0, cache_write_5min_per_million=1.0, cache_write_1h_per_million=2.0
        )
        assert pricing.write_rate_for_ttl("1H") == 2.0
        assert pricing.write_rate_for_ttl("5M") == 1.0

    def test_single_tier_rate_still_wins_over_tiers_on_fallback(self) -> None:
        pricing = ModelPricing(
            0.0, 0.0, cache_write_per_million=3.0, cache_write_1h_per_million=6.0
        )
        assert pricing.write_rate_for_ttl(None) == 3.0


class TestLimitsAndBudgets:
    @staticmethod
    def _tracker(turns: int, model: str = "boundary-cost") -> UsageTracker:
        register_pricing(model, ModelPricing(100_000.0, 0.0))
        tracker = UsageTracker()
        for _ in range(turns):
            tracker.record_turn(model, UsageSnapshot(input_tokens=1))
        return tracker

    def test_float_noise_is_not_a_budget_overrun(self) -> None:
        tracker = self._tracker(3)  # three $0.10 turns
        assert tracker.is_over_budget(0.3) is False
        tracker.check_limits(UsageLimits(cost_usd_max=0.3))

    def test_real_overrun_still_detected(self) -> None:
        tracker = self._tracker(3)
        assert tracker.is_over_budget(0.2999) is True
        assert tracker.is_over_budget(0.0) is True

    def test_cost_limit_reports_dollars(self) -> None:
        tracker = self._tracker(25)
        with pytest.raises(UsageLimitExceededError) as caught:
            tracker.check_limits(UsageLimits(cost_usd_max=1.0))
        assert caught.value.limit_name == "cost_usd"
        assert caught.value.observed == pytest.approx(2.5)
        assert caught.value.ceiling == 1.0
        assert "observed 2.5" in str(caught.value)

    def test_small_cost_ceiling_is_not_truncated_to_zero(self) -> None:
        tracker = self._tracker(1)
        with pytest.raises(UsageLimitExceededError) as caught:
            tracker.check_limits(UsageLimits(cost_usd_max=1e-7))
        assert caught.value.ceiling == 1e-7


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: BudgetExceededError(1.0, 2.0),
        lambda: UsageLimitExceededError("total_tokens", 5, 1),
        lambda: UsageLimitExceededError("cost_usd", 2.5, 1.0),
        lambda: PricingAlreadyRegisteredError("m"),
        lambda: InvalidTransitionError(AgentStatus.RUNNING, AgentStatus.IDLE),
    ],
    ids=["budget", "limit", "limit-cost", "pricing", "transition"],
)
def test_exceptions_round_trip(make_error) -> None:
    # copy.copy rebuilds through __reduce_ex__, the same path pickle takes.
    error = make_error()
    copied = copy.copy(error)
    assert type(copied) is type(error)
    assert str(copied) == str(error)


class TestEvents:
    def test_with_failure_class_syncs_status(self) -> None:
        event = AgentEvent.failed("r", "p", AgentFailureClass.TOOL_ERROR)
        cancelled = event.with_failure_class(AgentFailureClass.CANCELLED)
        assert cancelled.status == AgentEventStatus.CANCELLED
        restored = cancelled.with_failure_class(AgentFailureClass.TOOL_ERROR)
        assert restored.status == AgentEventStatus.FAILED

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
    def test_to_json_is_strict(self, value: float) -> None:
        event = AgentEvent.ready("r", "p", data={"x": {"y": [value]}})

        def reject(name: str) -> None:
            raise AssertionError(f"non-JSON constant {name}")

        decoded = json.loads(event.to_json(), parse_constant=reject)
        assert decoded["data"]["x"]["y"] == [str(value)]

    def test_to_json_still_reports_circular_data(self) -> None:
        data: dict[str, object] = {}
        data["self"] = data
        with pytest.raises(ValueError, match="Circular"):
            AgentEvent.ready("r", "p", data=data).to_json()

    def test_turn_events(self) -> None:
        ok = AgentEvent.turn_completed(
            "r", "p", model="m", duration_seconds=1.5, data={"extra": 1}
        )
        assert ok.event == AgentEventName.AGENT_TURN_COMPLETED
        assert ok.status == AgentEventStatus.COMPLETED
        assert ok.data == {"extra": 1, "model": "m", "duration_seconds": 1.5}

        bad = AgentEvent.turn_failed(
            "r", "p", model="", duration_seconds=0, error_type="TimeoutError"
        )
        assert bad.event == AgentEventName.AGENT_TURN_FAILED
        assert bad.status == AgentEventStatus.FAILED
        assert bad.data == {
            "model": "",
            "duration_seconds": 0,
            "error_type": "TimeoutError",
        }
        assert AgentEvent.from_json(bad.to_json()) == bad

    @pytest.mark.parametrize("duration", [-1.0, float("nan")])
    def test_turn_event_rejects_bad_duration(self, duration: float) -> None:
        with pytest.raises(ValueError):
            AgentEvent.turn_completed("r", "p", model="m", duration_seconds=duration)


class TestStreamEvents:
    def test_is_hashable(self) -> None:
        event = StreamEvent.text("a")
        assert hash(event) == hash(event)

    def test_tool_result_to_json_serializes_mcp_blocks(self) -> None:
        pytest.importorskip("mcp")
        from mcp.types import TextContent

        event = StreamEvent.tool_result("t", [TextContent(type="text", text="hi")])
        decoded = json.loads(event.to_json())
        assert decoded["payload"]["result"][0]["text"] == "hi"

    def test_tool_result_to_json_falls_back_to_str(self) -> None:
        decoded = json.loads(
            StreamEvent.tool_result(
                "t",
                {
                    1,
                },
            ).to_json()
        )
        assert decoded["payload"]["result"] == "{1}"

    def test_to_json_is_strict(self) -> None:
        event = StreamEvent.tool_result("t", float("nan"))
        assert json.loads(event.to_json())["payload"]["result"] == "nan"


def test_registry_evicts_only_terminal_workers() -> None:
    registry = AgentRegistry()
    done = registry.create("a", "p")
    done.transition(AgentStatus.CANCELLED)
    active = registry.create("b", "p")
    assert registry.evict_terminal() == 1
    assert registry.get(done.worker_id) is None
    assert registry.get(active.worker_id) is active
    assert registry.evict_terminal() == 0


class TestOtel:
    @pytest.fixture
    def sink(self):
        pytest.importorskip("opentelemetry")
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        from techrevati.runtime.otel import OpenTelemetrySink

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        sink = OpenTelemetrySink(tracer=provider.get_tracer("t"), provider_name="acme")
        sink.exporter = exporter  # type: ignore[attr-defined]
        return sink

    @staticmethod
    def _by_name(sink) -> dict[str, object]:
        return {s.name: s for s in sink.exporter.get_finished_spans()}

    def test_parent_span_name_matches_its_operation(self, sink) -> None:
        sink.emit(AgentEvent.started("r", "p"))
        sink.emit(AgentEvent.completed("r", "p"))
        spans = self._by_name(sink)
        assert list(spans) == ["invoke_agent r"]
        assert spans["invoke_agent r"].attributes["gen_ai.operation.name"] == (
            "invoke_agent"
        )

    def test_tool_span_carries_semantic_tool_name(self, sink) -> None:
        sink.emit(AgentEvent.tool_called("r", "p", "search"))
        sink.emit(AgentEvent.tool_completed("r", "p", "search"))
        span = self._by_name(sink)["execute_tool search"]
        assert span.attributes["gen_ai.tool.name"] == "search"

    def test_agent_span_nests_under_its_phase(self, sink) -> None:
        sink.emit(AgentEvent.phase_started("p"))
        sink.emit(AgentEvent.started("r", "p"))
        sink.emit(AgentEvent.completed("r", "p"))
        sink.emit(
            AgentEvent(
                event=AgentEventName.PHASE_COMPLETED,
                status=AgentEventStatus.COMPLETED,
                phase="p",
            )
        )
        spans = self._by_name(sink)
        agent, phase = spans["invoke_agent r"], spans["invoke_workflow"]
        assert agent.parent is not None
        assert agent.parent.span_id == phase.context.span_id
        assert agent.context.trace_id == phase.context.trace_id

    def test_post_guardrail_block_closes_tool_span(self, sink) -> None:
        sink.emit(AgentEvent.started("r", "p"))
        sink.emit(AgentEvent.tool_called("r", "p", "search"))
        sink.emit(
            AgentEvent.blocked(
                "r", "p", data={"tool": "search", "kind": "guardrail", "stage": "post"}
            )
        )
        span = self._by_name(sink)["execute_tool search"]
        assert span.attributes["error.type"] == "guardrail_blocked"
        assert sink._active_tool_spans == {}

    def test_pre_guardrail_block_leaves_concurrent_call_open(self, sink) -> None:
        sink.emit(AgentEvent.started("r", "p"))
        sink.emit(AgentEvent.tool_called("r", "p", "search"))
        sink.emit(
            AgentEvent.blocked(
                "r", "p", data={"tool": "search", "kind": "guardrail", "stage": "pre"}
            )
        )
        assert len(sink._active_tool_spans[("r", "p", "search")]) == 1
        sink.shutdown()

    def test_turn_events_become_backdated_chat_spans(self, sink) -> None:
        from opentelemetry.trace import SpanKind, StatusCode

        sink.emit(AgentEvent.started("r", "p"))
        sink.emit(AgentEvent.turn_completed("r", "p", model="m", duration_seconds=2.0))
        sink.emit(
            AgentEvent.turn_failed(
                "r", "p", model="m", duration_seconds=0.5, error_type="TimeoutError"
            )
        )
        sink.emit(AgentEvent.completed("r", "p"))
        spans = sink.exporter.get_finished_spans()
        ok, failed = [s for s in spans if s.name == "chat m"]
        parent = self._by_name(sink)["invoke_agent r"]
        assert ok.kind == SpanKind.CLIENT
        assert ok.parent.span_id == parent.context.span_id
        assert ok.attributes["gen_ai.operation.name"] == "chat"
        assert ok.attributes["gen_ai.request.model"] == "m"
        assert ok.end_time - ok.start_time == 2_000_000_000
        assert "error.type" not in ok.attributes
        assert failed.attributes["error.type"] == "TimeoutError"
        assert failed.status.status_code == StatusCode.ERROR

    def test_shutdown_ends_open_spans_and_is_idempotent(self, sink) -> None:
        sink.emit(AgentEvent.started("r", "p"))
        sink.emit(AgentEvent.tool_called("r", "p", "search"))
        sink.shutdown()
        sink.shutdown()
        spans = sink.exporter.get_finished_spans()
        assert {s.attributes["error.type"] for s in spans} == {"abrupt_termination"}
        assert len(spans) == 2

    def test_batch_exporter_receives_orphans_at_exit(self) -> None:
        """Provider created before the sink, module imported before the provider."""
        pytest.importorskip("opentelemetry")
        script = textwrap.dedent(
            """
            import sys
            from techrevati.runtime import AgentEvent
            from techrevati.runtime.otel import OpenTelemetrySink
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor, SpanExporter, SpanExportResult,
            )

            class Print(SpanExporter):
                def export(self, spans):
                    for span in spans:
                        print("EXPORTED", span.attributes.get("error.type"), flush=True)
                    return SpanExportResult.SUCCESS

            provider = TracerProvider()
            provider.add_span_processor(BatchSpanProcessor(Print()))
            sink = OpenTelemetrySink(tracer=provider.get_tracer("t"))
            sink.emit(AgentEvent.started("r", "p"))
            sys.exit(0)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        assert "EXPORTED abrupt_termination" in result.stdout


class TestOtelMetrics:
    @pytest.fixture
    def meter(self):
        pytest.importorskip("opentelemetry")
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        reader = InMemoryMetricReader()
        meter = MeterProvider(metric_readers=[reader]).get_meter("t")
        meter.reader = reader  # type: ignore[attr-defined]
        return meter

    @staticmethod
    def _points(meter) -> dict[str, list]:
        data = meter.reader.get_metrics_data()
        found: dict[str, list] = {}
        if data is None:
            return found
        for resource in data.resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    found[metric.name] = list(metric.data.data_points)
        return found

    def test_usage_counters_follow_current_conventions(self, meter) -> None:
        from techrevati.runtime.otel import OpenTelemetryUsageSink

        sink = OpenTelemetryUsageSink(meter=meter, provider_name="acme")
        sink.record(
            "m",
            UsageSnapshot(
                input_tokens=10,
                output_tokens=5,
                cache_read_tokens=1000,
                cache_write_tokens=500,
            ),
            0.0,
        )
        points = self._points(meter)
        prefix = "gen_ai.client.inference.usage."
        assert points[f"{prefix}input_tokens"][0].value == 1510
        assert points[f"{prefix}output_tokens"][0].value == 5
        assert points[f"{prefix}cache_read.input_tokens"][0].value == 1000
        assert points[f"{prefix}cache_write.input_tokens"][0].value == 500
        assert dict(points[f"{prefix}input_tokens"][0].attributes) == {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "acme",
            "gen_ai.request.model": "m",
            "gen_ai.token.modality": "unknown",
        }
        assert "gen_ai.client.token.usage" in points

    def test_zero_amounts_are_not_measured(self, meter) -> None:
        from techrevati.runtime.otel import OpenTelemetryUsageSink

        OpenTelemetryUsageSink(meter=meter).record(
            "m", UsageSnapshot(input_tokens=1), 0.0
        )
        points = self._points(meter)
        assert "gen_ai.client.inference.usage.output_tokens" not in points
        assert "gen_ai.client.inference.usage.cache_read.input_tokens" not in points

    def test_legacy_token_metric_can_be_disabled(self, meter) -> None:
        from techrevati.runtime.otel import OpenTelemetryUsageSink

        OpenTelemetryUsageSink(meter=meter, emit_legacy_token_metric=False).record(
            "m", UsageSnapshot(input_tokens=1), 0.0
        )
        points = self._points(meter)
        assert "gen_ai.client.token.usage" not in points
        assert "gen_ai.client.inference.usage.input_tokens" in points

    def test_inference_duration(self, meter) -> None:
        from techrevati.runtime.otel import OpenTelemetryMetricsSink

        sink = OpenTelemetryMetricsSink(meter=meter, provider_name="acme")
        sink.emit(AgentEvent.turn_completed("r", "p", model="m", duration_seconds=1.0))
        sink.emit(
            AgentEvent.turn_failed(
                "r", "p", model="m", duration_seconds=2.0, error_type="TimeoutError"
            )
        )
        points = self._points(meter)["gen_ai.client.inference.duration"]
        by_error = {p.attributes.get("error.type"): p for p in points}
        assert by_error[None].sum == 1.0
        assert by_error["TimeoutError"].sum == 2.0
        assert dict(by_error[None].attributes) == {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "acme",
            "gen_ai.request.model": "m",
        }
        # The advisory parameter is newer than the [otel] floor.
        from opentelemetry.metrics import Meter

        parameters = inspect.signature(Meter.create_histogram).parameters
        if "explicit_bucket_boundaries_advisory" in parameters:
            assert tuple(by_error[None].explicit_bounds)[:3] == (0.01, 0.02, 0.04)
            assert tuple(by_error[None].explicit_bounds)[-1] == 81.92

    def test_tool_duration(self, meter) -> None:
        from techrevati.runtime.otel import OpenTelemetryMetricsSink

        sink = OpenTelemetryMetricsSink(meter=meter)
        data = {"tool": "search", "duration_seconds": 0.5}
        sink.emit(AgentEvent.tool_completed("r", "p", "search").with_data(data))
        sink.emit(
            AgentEvent.failed("r", "p", AgentFailureClass.TOOL_ERROR).with_data(data)
        )
        sink.emit(
            AgentEvent.failed("r", "p", AgentFailureClass.CANCELLED).with_data(data)
        )
        points = self._points(meter)["gen_ai.execute_tool.duration"]
        by_error = {p.attributes.get("error.type"): p for p in points}
        assert by_error[None].count == 2  # completed + cancelled (not an error)
        assert by_error["tool_error"].count == 1
        assert by_error[None].attributes["gen_ai.tool.name"] == "search"
        assert by_error[None].attributes["gen_ai.agent.name"] == "r"
        assert by_error[None].attributes["gen_ai.operation.name"] == "execute_tool"

    @pytest.mark.parametrize(
        "event",
        [
            AgentEvent.tool_completed("r", "p", "search"),
            AgentEvent.started("r", "p"),
            AgentEvent.ready("r", "p", data={"duration_seconds": True}),
            AgentEvent.ready("r", "p", data={"tool": "t", "duration_seconds": 1.0}),
            AgentEvent.tool_completed("r", "p", "t").with_data(
                {"tool": "t", "duration_seconds": float("nan")}
            ),
        ],
    )
    def test_events_without_usable_duration_are_ignored(self, meter, event) -> None:
        from techrevati.runtime.otel import OpenTelemetryMetricsSink

        OpenTelemetryMetricsSink(meter=meter).emit(event)
        assert self._points(meter) == {}

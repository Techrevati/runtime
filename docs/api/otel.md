# Telemetry Integration

Requires `pip install 'techrevati-runtime[otel]'`.

`OpenTelemetrySink` keeps one parent span open for the agent or phase
lifecycle. Tool calls open child spans under that parent, and
tool-scoped failures close only the tool span. Terminal session
failures still close the parent span.

Event details are omitted from spans by default because callers may put prompt
fragments, tool arguments, or exception text into `AgentEvent.detail`. Use
`OpenTelemetrySink(include_event_detail=True)` only after detail values are
sanitized and telemetry retention is approved.

## GenAI semantic conventions

The sink emits OpenTelemetry GenAI semantic-convention signals:

- **Spans** carry `gen_ai.operation.name`, `gen_ai.provider.name`, and
  `gen_ai.agent.name`. An `invoke_agent {role}` span nests under the open
  `invoke_workflow` span of its phase, when there is one. Tool calls are
  `execute_tool {tool}` children with `gen_ai.tool.name` (concurrent calls to the
  same tool each get their own span via a per-key LIFO stack). Model turns are
  `chat {model}` client spans backdated by the measured duration.
- **Token metrics** (`OpenTelemetryUsageSink`):
  `gen_ai.client.inference.usage.input_tokens` (including cached tokens),
  `.output_tokens`, `.cache_read.input_tokens` and `.cache_write.input_tokens`
  counters, plus a `techrevati.cost.usd` counter (no standard GenAI cost metric
  exists yet).
- **Legacy token metric**: the conventions have removed `gen_ai.client.token.usage`.
  It stays on by default so existing dashboards keep working; turn it off with
  `OpenTelemetryUsageSink(emit_legacy_token_metric=False)` once they read the
  counters.
- **Duration metrics** (`OpenTelemetryMetricsSink`, an event sink):
  `gen_ai.client.inference.duration` and `gen_ai.execute_tool.duration` histograms
  in seconds, with `error.type` on failure. The conventions' bucket boundaries are
  applied as an advisory on OpenTelemetry API versions that support it; older ones
  use the SDK's default buckets.
- **Message bodies**: if a caller places `gen_ai.input.messages` /
  `gen_ai.output.messages` in `AgentEvent.data`, they are emitted as span events
  of the same name — but only when `include_event_detail=True`, since message
  content is sensitive. The runtime does not own the model call, so it never
  fabricates these; they appear only when the caller supplies them.

These conventions now live in the
[`semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai)
repository and are still marked *Development*, so names can change.

Two required attributes cannot be known exactly by the runtime:

- `gen_ai.provider.name`: the runtime does not make the model call. Pass the real
  provider as `provider_name`; the default `techrevati` only marks the data's source.
- `gen_ai.token.modality`: always `unknown`, as the conventions require when
  `UsageSnapshot` does not split tokens by modality.

## Process exit

Spans still open at normal interpreter exit are ended with
`error.type=abrupt_termination`. That hook does not run on a default SIGTERM,
`os._exit` or a hard crash, and a span ended there only reaches the exporter if the
`TracerProvider` shuts down afterwards, so create the provider before the first
sink. For SIGTERM, or a provider created later, call `sink.shutdown()` yourself.

::: techrevati.runtime.otel

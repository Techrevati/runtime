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
  `gen_ai.agent.name`, with per-tool child spans nested under the agent/phase
  parent (concurrent calls to the same tool each get their own span via a
  per-key LIFO stack).
- **Metrics** (`OpenTelemetryUsageSink`): a `gen_ai.client.token.usage` histogram
  discriminated by `gen_ai.token.type` (`input`/`output`) and
  `gen_ai.request.model`, plus a `techrevati.cost.usd` counter (no standard GenAI
  cost metric exists yet).
- **Message bodies**: if a caller places `gen_ai.input.messages` /
  `gen_ai.output.messages` in `AgentEvent.data`, they are emitted as span events
  of the same name — but only when `include_event_detail=True`, since message
  content is sensitive. The runtime does not own the model call, so it never
  fabricates these; they appear only when the caller supplies them.

::: techrevati.runtime.otel

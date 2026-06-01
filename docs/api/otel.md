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

::: techrevati.runtime.otel

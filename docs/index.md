# Runtime

Author: Techrevati doo

Runtime primitives for multi-step LLM agent loops.

The runtime provides sync and async sessions, lifecycle tracking,
retry classification, circuit breakers, usage tracking, permissions,
guardrails, policy evaluation, checkpointing, rate limiting, streaming, hooks,
and telemetry sinks.

The package is currently `0.3.0rc1`. This is a release candidate; the `0.x`
API surface remains unstable, so pin exact versions when you depend on a
specific behavior.

```bash
pip install techrevati-runtime
```

## What's Included

- `AgentSession` / `Orchestrator` compatibility alias
- Retry policy
- Circuit breaker
- Usage tracking
- Quality gate
- Agent lifecycle
- Agent events
- Permissions
- Guardrails
- Policy engine
- Checkpointing
- Rate limiting
- Routing
- Streaming
- Hooks
- Durable sinks

## Design Tenets

- Zero runtime dependencies by default.
- Type-safe public API with `py.typed`.
- Thread-safe sync paths and async-safe async paths.
- Caller-owned configuration for prices, thresholds, roles, sinks, and policy.

## License

MIT. Copyright 2026 Techrevati doo.

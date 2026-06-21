# Runtime

Author: Techrevati doo

Runtime primitives for multi-step LLM agent loops.

The runtime provides sync and async sessions, lifecycle tracking,
retry classification, circuit breakers, usage tracking, permissions,
guardrails, policy evaluation, checkpointing, rate limiting, streaming, hooks,
telemetry sinks, typed outputs, session memory, an MCP tool adapter, and an
EU AI Act compliance kit.

The package is currently `0.4.0`. The `0.x`
API surface remains unstable, so pin exact versions when you depend on a
specific behavior.

```bash
pip install techrevati-runtime
```

## What's Included

- `AgentSession` (sync and async orchestration sessions)
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
- Typed outputs (`OutputSpec`)
- Session memory and compaction
- Step-level durability
- MCP tool adapter (`[mcp]` extra)
- EU AI Act compliance kit (audit log, human oversight, risk registry, incidents, transparency)

## Design Tenets

- Zero runtime dependencies by default.
- Type-safe public API with `py.typed`.
- Thread-safe sync paths and async-safe async paths.
- Caller-owned configuration for prices, thresholds, roles, sinks, and policy.

## License

MIT. Copyright 2026 Techrevati doo.

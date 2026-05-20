# Security Policy

## Reporting a vulnerability

If you believe you have found a security issue in
`techrevati-runtime`, **do not open a public issue**. Instead, email
security@techrevati.com with:

- A description of the vulnerability.
- Steps to reproduce.
- Affected version(s) (output of `pip show techrevati-runtime`).
- Suggested fix or mitigation, if you have one.

We aim to acknowledge within 3 business days and to publish a patched
release within 14 days for High/Critical issues. Coordinated disclosure
windows can be extended on request.

## Threat model

`techrevati-runtime` is a **library**, not a service. It runs in the
same process as the caller and has access to whatever the caller has
access to. The primitives below are deliberately *advisory*:

### `PermissionEnforcer`

`run_tool(tool_name, fn)` checks the configured permission policy
before invoking `fn`. This is a **gate**, not a sandbox.

- It does NOT contain a misbehaving `fn` once invoked.
- It does NOT prevent the caller from invoking `fn` directly without
  going through `run_tool`. (Callers are trusted.)
- It does NOT inspect or filter `fn`'s arguments — those are
  caller-controlled.

Use OS-level sandboxing (containers, seccomp, gVisor) if you need
real isolation.

### `Guardrail`

Guardrails check pre-call (role + tool name) and post-call (return
value) context. Like permissions, they are advisory: they cannot
prevent a caller from running the tool body directly.

### `Orchestrator(budget_usd=...)`

Budget enforcement is `O(turns)` — the check happens after each
recorded turn, not on per-token estimates. A single turn can exceed
the budget by up to one turn's worth of cost. Use `max_iterations`
in combination with per-turn `usage` ceilings if you need a tighter
upper bound.

### `CircuitBreaker`

The breaker is per-process. It does NOT coordinate across replicas;
each instance counts its own failures. Pair with a shared rate
limiter or external coordinator if you need fleet-wide breaker state.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | ✅ (current pre-release; security fixes on best-effort basis) |
| 0.0.x | ⚠ Critical-only fixes until 2026-09-30 |
| < 0.0.0 | Not applicable |

Once 0.2.0 ships (stable beta), 0.1.x will be supported for 6 months.

## Known limitations

These are documented behaviors, not vulnerabilities, but they affect
how you should deploy:

- Pricing data is caller-provided. The runtime cannot prevent
  cost-inflation attacks where an attacker convinces the caller to
  register lower-than-actual prices. Validate `pricing.json` sources.
- `RingBufferEventSink` and `RingBufferUsageSink` drop oldest entries
  on overflow. Plug a durable sink (OpenTelemetry, your message bus)
  for compliance/audit trails.
- The runtime trusts `UsageSnapshot` values its callers provide.
  Token counts are not validated against the model's actual response.

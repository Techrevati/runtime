# Robustness & cybersecurity — Article 15

> ⚠️ **Not legal advice.** A first line of defense, not a substitute for a
> hardened deployment environment.

Article 15 requires resilience against unauthorised attempts to alter the
system's use, outputs, or performance. The runtime already ships
`PromptInjectionGuardrail`, `CircuitBreaker`, and `RateLimiter`; the compliance
kit adds two integrity primitives.

## OutputIntegrityGuardrail (tool outputs)

A `Guardrail` (runs at `post`) that flags NUL bytes, C0 / C1 / ANSI control escape
sequences (used to smuggle terminal payloads), DEL, and oversized output. It walks
nested `str` / `bytes` / `dict` / `list` / `tuple` / `set` structures, including
dict keys, and `max_chars` caps the payload's total size across all of them.

Bytes that decode as UTF-8 are scanned as text. Other bytes are treated as opaque
binary (images and archives legitimately contain control bytes) and only count
towards the size cap. Objects of other types are not inspected.

```python
from techrevati.runtime.compliance import OutputIntegrityGuardrail

guardrail = OutputIntegrityGuardrail(max_chars=100_000)
# attach via AgentSession(guardrails=[guardrail]) or the kit (enabled by default)
```

## InputSanitizationHook (prompts & tool args)

A `Hook` (not a Guardrail) — only hooks see `ctx.prompt` and `ctx.args`; the
`Guardrail.check_pre` signature does not receive tool inputs. It scans before the
model call and before tool execution, raising `InputSanitizationError` on a hit.
It can only scan what the session is given: tool arguments captured in a closure
are invisible to it.

```python
from techrevati.runtime.compliance import InputSanitizationHook

hook = InputSanitizationHook(max_chars=100_000)
# attach via AgentSession(hooks=[hook]) or the kit (enabled by default)
```

`EUAIActComplianceKit.standard()` enables both by default
(`sanitize_inputs=True`, `check_output_integrity=True`).

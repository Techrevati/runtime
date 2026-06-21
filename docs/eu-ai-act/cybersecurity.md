# Robustness & cybersecurity — Article 15

> ⚠️ **Not legal advice.** A first line of defense, not a substitute for a
> hardened deployment environment.

Article 15 requires resilience against unauthorised attempts to alter the
system's use, outputs, or performance. The runtime already ships
`PromptInjectionGuardrail`, `CircuitBreaker`, and `RateLimiter`; the compliance
kit adds two integrity primitives.

## OutputIntegrityGuardrail (tool outputs)

A `Guardrail` (runs at `post`) that flags NUL bytes, C0 / ANSI control escape
sequences (used to smuggle terminal payloads), and oversized output. It walks
nested `str` / `dict` / `list` structures.

```python
from techrevati.runtime.compliance import OutputIntegrityGuardrail

guardrail = OutputIntegrityGuardrail(max_chars=100_000)
# attach via AgentSession(guardrails=[guardrail]) or the kit (enabled by default)
```

## InputSanitizationHook (prompts & tool args)

A `Hook` (not a Guardrail) — only hooks see `ctx.prompt` and `ctx.args`; the
`Guardrail.check_pre` signature does not receive tool inputs. It scans before the
model call and before tool execution, raising `InputSanitizationError` on a hit.

```python
from techrevati.runtime.compliance import InputSanitizationHook

hook = InputSanitizationHook(max_chars=100_000)
# attach via AgentSession(hooks=[hook]) or the kit (enabled by default)
```

`EUAIActComplianceKit.standard()` enables both by default
(`sanitize_inputs=True`, `check_output_integrity=True`).

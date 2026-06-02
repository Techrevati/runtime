# Typed outputs

An `OutputSpec[T]` turns the opaque string a model returns into a typed,
validated value. Specs are pure and caller-applied — the runtime does not own the
model call, so you parse the result yourself.

```python
from techrevati.runtime import AgentSession, JsonOutputSpec, OutputValidationError

spec = JsonOutputSpec(required_keys=("decision", "reason"))

agent = AgentSession(role="assessor", phase="decide")
with agent.session() as session:
    raw, _usage = session.run_turn(lambda: call_model(prompt))
    try:
        decision = spec.parse(raw)        # dict with "decision" + "reason"
    except OutputValidationError:
        ...                               # re-prompt, fall back, or fail the turn
```

Reference implementations:

- **`JsonOutputSpec`** — parse JSON, optionally enforce `required_keys` and a
  top-level `require_type`; `strip_fences=True` (default) unwraps Markdown code
  fences models often add.
- **`RegexOutputSpec`** — match a regex and return its named groups as a dict.
- **`CallableOutputSpec`** — wrap any `Callable[[str], T]`; its exceptions are
  normalized to `OutputValidationError`.

Anything that does not satisfy the spec raises `OutputValidationError`.

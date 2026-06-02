# Session memory

`ConversationMemory` accumulates a turn-by-turn message history and applies a
**compaction strategy** after each append, so a long session stays within the
model's context window. Memory is caller-driven: append what you send and
receive, then read `messages()` to build the next prompt.

```python
from techrevati.runtime import (
    InMemoryConversationMemory, MemoryMessage, TokenBudgetCompaction,
)

memory = InMemoryConversationMemory(
    compaction=TokenBudgetCompaction(max_tokens=8000),
)
memory.add(MemoryMessage("system", "You are a loan assistant."))
memory.add(MemoryMessage("user", question))

reply, _ = session.run_turn(lambda: call_model(memory.messages()))
memory.add(MemoryMessage("assistant", reply))
```

Compaction strategies:

- **`NoCompaction`** — keep everything (default).
- **`WindowCompaction(max_messages=..., keep_system=True)`** — keep the last *N*
  messages; system messages are retained first.
- **`TokenBudgetCompaction(max_tokens=..., estimator=...)`** — drop oldest
  messages until an estimated token budget is met. System messages are retained
  first; if they alone exceed the budget they are all kept — correctness over
  budget, never silently dropping instructions.

Write a custom strategy by implementing `CompactionStrategy.compact`.

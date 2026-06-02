"""
Session memory — conversation history with compaction.

``ConversationMemory`` is a small protocol for accumulating a turn-by-turn message
history that a caller feeds back into the model. Long sessions outgrow the model's
context window, so memory applies a **compaction strategy** after each append:

- :class:`NoCompaction` — keep everything (default).
- :class:`WindowCompaction` — keep the last *N* messages (optionally always
  retaining ``system`` messages).
- :class:`TokenBudgetCompaction` — drop oldest messages until an estimated token
  budget is met (system messages retained first).

The runtime does not own the model call, so memory is caller-driven: append what
you send and receive, then read ``messages()`` to build the next prompt.

    memory = InMemoryConversationMemory(
        compaction=TokenBudgetCompaction(max_tokens=8000)
    )
    memory.add(MemoryMessage("system", "You are a loan assistant."))
    memory.add(MemoryMessage("user", question))
    reply, _ = session.run_turn(lambda: call_model(memory.messages()))
    memory.add(MemoryMessage("assistant", reply))
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "CompactionStrategy",
    "ConversationMemory",
    "InMemoryConversationMemory",
    "MemoryMessage",
    "NoCompaction",
    "TokenBudgetCompaction",
    "WindowCompaction",
]


@dataclass(frozen=True)
class MemoryMessage:
    """One conversation message."""

    role: str
    content: str
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("MemoryMessage.role must be a non-empty string")
        if not isinstance(self.content, str):
            raise TypeError("MemoryMessage.content must be a string")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.metadata is not None:
            d["metadata"] = dict(self.metadata)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryMessage:
        return cls(
            role=data["role"],
            content=data["content"],
            metadata=data.get("metadata"),
        )


@runtime_checkable
class CompactionStrategy(Protocol):
    """Reduce a message list to fit a budget. Pure; returns a new list."""

    def compact(self, messages: list[MemoryMessage]) -> list[MemoryMessage]: ...


@runtime_checkable
class ConversationMemory(Protocol):
    """Accumulates conversation messages with compaction."""

    def add(self, message: MemoryMessage) -> None: ...

    def messages(self) -> list[MemoryMessage]: ...

    def clear(self) -> None: ...


def _default_estimator(text: str) -> int:
    """~4 characters per token heuristic (matches TokenBudgetCheckHook)."""
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class NoCompaction:
    """Keep every message."""

    def compact(self, messages: list[MemoryMessage]) -> list[MemoryMessage]:
        return list(messages)


@dataclass(frozen=True)
class WindowCompaction:
    """Keep the most recent ``max_messages`` (system messages retained first)."""

    max_messages: int
    keep_system: bool = True

    def __post_init__(self) -> None:
        if self.max_messages <= 0:
            raise ValueError("max_messages must be positive")

    def compact(self, messages: list[MemoryMessage]) -> list[MemoryMessage]:
        if len(messages) <= self.max_messages:
            return list(messages)
        if not self.keep_system:
            return list(messages[-self.max_messages :])
        system = [m for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        budget = max(0, self.max_messages - len(system))
        kept_rest = rest[-budget:] if budget else []
        # Preserve original ordering.
        kept = set(map(id, system)) | set(map(id, kept_rest))
        return [m for m in messages if id(m) in kept]


@dataclass(frozen=True)
class TokenBudgetCompaction:
    """Drop oldest messages until the estimated token budget is met.

    System messages are retained first; if they alone exceed the budget they are
    all kept (correctness over budget — never silently drop instructions).
    """

    max_tokens: int
    estimator: Callable[[str], int] = _default_estimator
    keep_system: bool = True

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

    def _cost(self, message: MemoryMessage) -> int:
        return self.estimator(message.content)

    def compact(self, messages: list[MemoryMessage]) -> list[MemoryMessage]:
        total = sum(self._cost(m) for m in messages)
        if total <= self.max_tokens:
            return list(messages)

        system = [m for m in messages if m.role == "system"] if self.keep_system else []
        system_ids = set(map(id, system))
        budget = self.max_tokens - sum(self._cost(m) for m in system)

        kept_recent: list[MemoryMessage] = []
        used = 0
        for message in reversed(messages):
            if id(message) in system_ids:
                continue
            cost = self._cost(message)
            if used + cost > budget:
                break
            kept_recent.append(message)
            used += cost
        keep_ids = system_ids | set(map(id, kept_recent))
        return [m for m in messages if id(m) in keep_ids]


class InMemoryConversationMemory:
    """Process-local conversation memory; compacts after each append."""

    def __init__(
        self,
        *,
        compaction: CompactionStrategy | None = None,
        initial: Iterable[MemoryMessage] = (),
    ) -> None:
        self._compaction: CompactionStrategy = compaction or NoCompaction()
        self._messages: list[MemoryMessage] = []
        for message in initial:
            self.add(message)

    def add(self, message: MemoryMessage) -> None:
        if not isinstance(message, MemoryMessage):
            raise TypeError("message must be a MemoryMessage")
        self._messages.append(message)
        self._messages = self._compaction.compact(self._messages)

    def messages(self) -> list[MemoryMessage]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

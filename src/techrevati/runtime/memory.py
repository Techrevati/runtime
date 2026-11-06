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

Both limiting strategies share two guarantees, even when that means exceeding the
limit: the newest message always survives (it is usually the question being
asked), and the kept window never starts on a ``role="tool"`` message, whose
originating call would otherwise be gone. Messages are selected by position, so
appending the same ``MemoryMessage`` instance repeatedly is safe.

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


def _window_start(messages: list[MemoryMessage], order: list[int], start: int) -> int:
    """Return a cut into ``order`` whose first kept message is not a tool result.

    Moves forward past orphaned tool results. If that would discard everything
    kept (the window holds only tool results), moves backward instead to pull in
    the message that issued them, because the newest message has to survive.
    """
    forward = start
    while forward < len(order) and messages[order[forward]].role == "tool":
        forward += 1
    if forward < len(order):
        return forward
    backward = start
    while backward > 0 and messages[order[backward]].role == "tool":
        backward -= 1
    return backward


@dataclass(frozen=True)
class NoCompaction:
    """Keep every message."""

    def compact(self, messages: list[MemoryMessage]) -> list[MemoryMessage]:
        return list(messages)


@dataclass(frozen=True)
class WindowCompaction:
    """Keep the most recent ``max_messages`` (system messages retained first).

    The newest message survives even if system messages alone fill the window,
    so the result can exceed ``max_messages``; see the module docstring.
    """

    max_messages: int
    keep_system: bool = True

    def __post_init__(self) -> None:
        if self.max_messages <= 0:
            raise ValueError("max_messages must be positive")

    def compact(self, messages: list[MemoryMessage]) -> list[MemoryMessage]:
        if len(messages) <= self.max_messages:
            return list(messages)
        retained = {
            i for i, m in enumerate(messages) if self.keep_system and m.role == "system"
        }
        order = [i for i in range(len(messages)) if i not in retained]
        budget = self.max_messages - len(retained)
        if budget <= 0:
            # A retained system message as newest already satisfies "newest
            # survives"; otherwise the newest non-system message takes one slot.
            budget = 0 if len(messages) - 1 in retained else 1
        if budget and order:
            start = max(0, len(order) - budget)
            retained.update(order[_window_start(messages, order, start) :])
        return [m for i, m in enumerate(messages) if i in retained]


@dataclass(frozen=True)
class TokenBudgetCompaction:
    """Drop oldest messages until the estimated token budget is met.

    System messages are retained first; if they alone exceed the budget they are
    all kept (correctness over budget — never silently drop instructions). The
    newest message is kept on the same principle, even when it alone exceeds the
    budget; see the module docstring.
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

        retained = {
            i for i, m in enumerate(messages) if self.keep_system and m.role == "system"
        }
        budget = self.max_tokens - sum(self._cost(messages[i]) for i in retained)
        order = [i for i in range(len(messages)) if i not in retained]

        start = len(order)
        used = 0
        for position in range(len(order) - 1, -1, -1):
            index = order[position]
            cost = self._cost(messages[index])
            if used + cost > budget and index != len(messages) - 1:
                break
            start = position
            used += cost
        if start < len(order):
            retained.update(order[_window_start(messages, order, start) :])
        return [m for i, m in enumerate(messages) if i in retained]


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

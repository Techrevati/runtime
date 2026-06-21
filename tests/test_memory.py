"""Tests for conversation memory + compaction."""

from __future__ import annotations

import pytest

from techrevati.runtime import (
    CompactionStrategy,
    ConversationMemory,
    InMemoryConversationMemory,
    MemoryMessage,
    NoCompaction,
    TokenBudgetCompaction,
    WindowCompaction,
)


def _msg(role: str, content: str) -> MemoryMessage:
    return MemoryMessage(role, content)


def test_message_validation_and_roundtrip() -> None:
    m = MemoryMessage("user", "hi", metadata={"id": 1})
    assert MemoryMessage.from_dict(m.to_dict()) == m
    with pytest.raises(ValueError):
        MemoryMessage("", "x")


def test_no_compaction_keeps_all() -> None:
    mem = InMemoryConversationMemory()
    for i in range(5):
        mem.add(_msg("user", str(i)))
    assert len(mem.messages()) == 5


def test_window_compaction_keeps_last_n() -> None:
    mem = InMemoryConversationMemory(
        compaction=WindowCompaction(max_messages=3, keep_system=False)
    )
    for i in range(6):
        mem.add(_msg("user", str(i)))
    assert [m.content for m in mem.messages()] == ["3", "4", "5"]


def test_window_compaction_retains_system() -> None:
    mem = InMemoryConversationMemory(
        compaction=WindowCompaction(max_messages=2, keep_system=True)
    )
    mem.add(_msg("system", "rules"))
    for i in range(4):
        mem.add(_msg("user", str(i)))
    msgs = mem.messages()
    assert msgs[0].role == "system"
    assert len(msgs) == 2  # system + 1 most-recent
    assert msgs[-1].content == "3"


def test_token_budget_compaction_trims_oldest() -> None:
    # estimator: 1 token per char for predictability
    strat = TokenBudgetCompaction(max_tokens=10, estimator=len)
    mem = InMemoryConversationMemory(compaction=strat)
    mem.add(_msg("user", "aaaaa"))  # 5
    mem.add(_msg("user", "bbbbb"))  # 5 -> total 10 ok
    mem.add(_msg("user", "ccccc"))  # 5 -> drop oldest
    contents = [m.content for m in mem.messages()]
    assert contents == ["bbbbb", "ccccc"]


def test_token_budget_retains_system_even_over_budget() -> None:
    strat = TokenBudgetCompaction(max_tokens=3, estimator=len, keep_system=True)
    mem = InMemoryConversationMemory(compaction=strat)
    mem.add(_msg("system", "longsystem"))  # 10 > budget but retained
    mem.add(_msg("user", "x"))
    msgs = mem.messages()
    assert any(m.role == "system" for m in msgs)


def test_clear_and_len() -> None:
    mem = InMemoryConversationMemory()
    mem.add(_msg("user", "a"))
    assert len(mem) == 1
    mem.clear()
    assert len(mem) == 0


def test_initial_messages_applied() -> None:
    mem = InMemoryConversationMemory(initial=[_msg("system", "s"), _msg("user", "u")])
    assert len(mem) == 2


def test_protocol_satisfaction() -> None:
    mem = InMemoryConversationMemory()
    assert isinstance(mem, ConversationMemory)
    assert isinstance(WindowCompaction(max_messages=1), CompactionStrategy)
    assert isinstance(NoCompaction(), CompactionStrategy)


def test_strategy_validation() -> None:
    with pytest.raises(ValueError):
        WindowCompaction(max_messages=0)
    with pytest.raises(ValueError):
        TokenBudgetCompaction(max_tokens=0)

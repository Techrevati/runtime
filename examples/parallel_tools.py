"""Parallel tool execution under ``asyncio.TaskGroup`` structured concurrency.

``arun_parallel_tools`` runs sibling coroutines under a TaskGroup so
any child failure cancels the rest — no orphan tasks, no swallowed
exceptions. ``timeout`` applies to the whole group.
"""

from __future__ import annotations

import asyncio

from techrevati.runtime import AgentSession


async def fetch_a() -> str:
    await asyncio.sleep(0.1)
    return "a"


async def fetch_b() -> str:
    await asyncio.sleep(0.05)
    return "b"


async def fetch_c() -> str:
    return "c"


async def main() -> None:
    agent = AgentSession(role="researcher", phase="gather")

    async with agent.asession() as session:
        results = await session.arun_parallel_tools(
            [fetch_a, fetch_b, fetch_c],
            timeout=1.0,
        )

    print(f"parallel results in input order: {results}")


if __name__ == "__main__":
    asyncio.run(main())

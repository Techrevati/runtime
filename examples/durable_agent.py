"""Durable agent session — SqliteSaver + thread_id + idempotency_key.

Pair an ``AgentSession`` with a ``SqliteSaver`` so a crashed process
can pick up where the previous one left off. The same recipe wires in
a ``RateLimiter`` and a ``ProviderRouter`` for completeness.

Run twice in a row to see resume-from-checkpoint in action:

    python examples/durable_agent.py
    python examples/durable_agent.py   # second run replays turn 1

The second invocation will print that turn 1 was replayed from the
checkpoint and only turn 2 will execute fresh.
"""

from __future__ import annotations

from pathlib import Path

from techrevati.runtime import (
    AgentSession,
    ModelPricing,
    RateLimiter,
    SqliteSaver,
    StaticProviderRouter,
    TokenBucket,
    UsageSnapshot,
    register_pricing,
)


def fake_model_call(prompt: str) -> str:
    """Stand-in for an LLM provider — replace with your own client call."""
    return f"draft for: {prompt}"


def main() -> None:
    register_pricing(
        "model-a",
        ModelPricing(input_per_million=3.0, output_per_million=15.0),
    )

    db_path = Path("./durable-session.db")
    saver = SqliteSaver(db_path)
    try:
        agent = AgentSession(
            role="writer",
            phase="draft",
            saver=saver,
            rate_limiter=RateLimiter(
                {
                    "rpm": TokenBucket("rpm", capacity=60, refill_per_second=1.0),
                    "input_tpm": TokenBucket(
                        "input_tpm", capacity=200_000, refill_per_second=3_333.0
                    ),
                }
            ),
            provider_router=StaticProviderRouter(("model-a", "model-b")),
        )

        with agent.session(thread_id="user-42:essay") as session:
            outline, _ = session.run_turn(
                lambda: fake_model_call("the runtime story"),
                model="model-a",
                usage=UsageSnapshot(input_tokens=200, output_tokens=900),
                idempotency_key="essay:outline",
            )
            print(f"turn 1 result: {outline}")

            revision, _ = session.run_turn(
                lambda: fake_model_call(f"polish: {outline}"),
                model="model-a",
                usage=UsageSnapshot(input_tokens=300, output_tokens=600),
                idempotency_key="essay:revision",
            )
            print(f"turn 2 result: {revision}")

        print(f"session summary: {session.summary()['usage']}")
        print(f"checkpoints stored: {len(saver.list('user-42:essay'))}")
    finally:
        saver.close()


if __name__ == "__main__":
    main()

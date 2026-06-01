"""tiny_agent — runnable companion to docs/tutorials/end-to-end.md.

Run with: ``python -m examples.tiny_agent``

This file mocks the model call and a tool — replace the two functions
marked ``# REPLACE`` with your real model client calls.
Everything else (lifecycle, cost tracking, retry, breaker,
permission gating, policy, handoff, telemetry) is real production code.
"""

from __future__ import annotations

import json
import logging

from techrevati.runtime import (
    AgentSession,
    CircuitBreaker,
    ModelPricing,
    PermissionEnforcer,
    PermissionMode,
    PermissionPolicy,
    RingBufferEventSink,
    RolePermissionConfig,
    UsageSnapshot,
    register_pricing,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


# ---- REPLACE these two with your real SDK calls ----


def call_model(prompt: str) -> str:
    """REPLACE: invoke your real LLM here."""
    return f"draft for: {prompt}"


def lookup_term(term: str) -> str:
    """REPLACE: any read-only tool body."""
    return f"definition of {term}"


# ---- Wiring (real production code) ----


def main() -> None:
    register_pricing(
        "your-model",
        ModelPricing(input_per_million=3.0, output_per_million=15.0),
    )

    breaker = CircuitBreaker(
        "model-api", failure_threshold=3, recovery_timeout_seconds=30.0
    )

    permissions = PermissionEnforcer(
        PermissionPolicy(
            role_configs={
                "writer": RolePermissionConfig("writer", PermissionMode.READ_ONLY),
                "editor": RolePermissionConfig("editor", PermissionMode.READ_ONLY),
            },
            tool_requirements={
                "lookup_term": PermissionMode.READ_ONLY,
                "write_db": PermissionMode.FULL_ACCESS,
            },
        )
    )

    events = RingBufferEventSink()
    agent = AgentSession(
        role="writer",
        phase="draft",
        project_id=42,
        circuit_breaker=breaker,
        permissions=permissions,
        event_sink=events,
        budget_usd=0.50,
        enforce_budget=True,
        max_iterations=5,
    )

    with agent.session() as session:
        # 1. Call the model.
        text, _usage = session.run_turn(
            lambda: call_model("write me an intro paragraph"),
            model="your-model",
            usage=UsageSnapshot(input_tokens=5_000, output_tokens=1_200),
            timeout=30.0,
        )
        print(f"writer drafted: {text!r}")

        # 2. Call a permitted tool through the gate.
        fact = session.run_tool("lookup_term", lambda: lookup_term("RAG"))
        print(f"writer looked up: {fact!r}")

        # 3. Hand off to editor.
        handoff = session.handoff_to(
            "editor", reason="needs polish", context={"draft": text}
        )
        print(f"handoff to {handoff.target_role}: {handoff.reason}")

    # 4. Editor picks up the handoff. Same registry so observability stays joined.
    editor_agent = AgentSession(
        role="editor",
        phase="draft",
        project_id=42,
        registry=agent.registry,
        permissions=permissions,
        event_sink=events,
        budget_usd=0.50,
        enforce_budget=True,
    )
    with editor_agent.session() as editor_session:
        review, _ = editor_session.run_turn(
            lambda: call_model(f"polish: {text}"),
            model="your-model",
            usage=UsageSnapshot(input_tokens=2_000, output_tokens=400),
        )
        print(f"editor produced: {review!r}")

    # 5. Inspect what happened.
    print()
    print("--- editor session summary ---")
    print(json.dumps(editor_session.summary(), indent=2, default=str))
    print()
    print(f"editor session cost: {editor_session.tracker.format_cost()}")
    print(f"events seen by sink: {len(events.events)}")


if __name__ == "__main__":
    main()

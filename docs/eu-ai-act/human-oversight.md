# Human oversight — Article 14

> ⚠️ **Not legal advice.** *Effective* oversight depends on reviewer competence
> and process, which this library cannot provide.

Article 14 requires that a natural person can interpret, intervene in, and stop
the system. `HumanOversightInterface` provides pause-for-review and manual
override, recording who decided and when into the Article 12 audit log.

## Pause for review

`pause_for_review` is a standalone async gate: you `await` it at a high-stakes
decision point and it returns the human's `ReviewDecision`. You supply a
`ReviewQueue` — the async bridge to your UI / Slack / ticketing system. The call
blocks your coroutine (not any worker) until a human responds or the timeout
elapses; it does **not** itself change the session's lifecycle status. If you want
the worker to show `AgentStatus.WAITING_FOR_INPUT` while it waits, transition it
around the `await` yourself.

```python
from techrevati.runtime.compliance import (
    HumanOversightInterface, ReviewerIdentity, ReviewDecision,
)

class SlackReviewQueue:
    async def enqueue(self, decision_id, context, timeout_seconds):
        # surface to a human; return a ReviewDecision or None on timeout
        ...

oversight = HumanOversightInterface(
    SlackReviewQueue(),
    require_review_for=("governance.breach",),
    default_timeout_seconds=1800,
    on_timeout="abort",          # or "proceed_with_warning"
    audit_log=kit.audit_log,
)

decision = await oversight.pause_for_review("loan-42", {"amount": 100_000})
if decision.decision == "reject":
    ...
```

The interface emits `oversight.review_requested` then `oversight.review_resolved`
(with the reviewer id) to the audit log. On an aborting timeout it raises
`ReviewTimeoutError`; with `proceed_with_warning` it returns a system-signed
approval and records the warning.

## Manual override (Article 14(4)(d))

```python
reviewer = ReviewerIdentity(id="alice@corp", role="approver",
                            authentication_method="oauth")
oversight.override("halt — anomalous outputs", reviewer, action="stop")
```

## Explaining a turn (Article 14(4)(c))

`ExplanationReport.from_events(turn_id, events)` produces a reviewer-readable
summary (role, phase, tools invoked, failures, final status) with `to_markdown()`.

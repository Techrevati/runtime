# Conformity self-check — Article 16

> ⚠️ **Not legal advice.** This is a self-check of technical controls, **not** a
> conformity assessment. A conformity assessment under Article 43 may require a
> notified body; this checklist does not substitute for it.

`EUAIActComplianceKit.conformity_assessment_checklist()` derives a yes/no
self-check from the kit's configuration. Items the kit can determine are marked
satisfied/unsatisfied; items requiring a deployer attestation are left manual.

```python
checklist = kit.conformity_assessment_checklist()
print(checklist.to_markdown())
```

Example output:

```
# EU AI Act conformity self-check

> ⚠️ Not legal advice. A self-check of technical controls, not a conformity
> assessment. ⬜ items require the deployer's attestation.

- ✅ Article 9 — Risk management system with acceptable residual risk
- ✅ Article 12 — Tamper-evident, retained record-keeping (audit log)
- ✅ Article 14 — Human oversight (pause / review / override) configured
- ✅ Article 15 — Robustness: input sanitization + output integrity
- ✅ Articles 26/73 — Incident detection + 15-day reporting tracking
- ⬜ (manual) Article 10 — Data governance for training/validation data
- ⬜ (manual) Article 13 — Instructions for use provided to the deployer
- ⬜ (manual) Article 16 — Conformity assessment completed and CE marking applied
```

The manual (⬜) items are deliberately outside the runtime's scope — data
governance, instructions-for-use delivery, and the formal conformity assessment
are deployer responsibilities the library cannot verify.

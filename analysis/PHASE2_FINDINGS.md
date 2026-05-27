# Phase 2 — Findings

## The corpus has structural gaps

The candidate brief promises "~30 PDF documents" but only **8** are provided in
[policies/](../policies/). The four travel-related ones are:

| Doc | Title | Subject |
|---|---|---|
| TEP-001 | Travel & Expense Overview | Principles, approval thresholds, audit, exceptions |
| TEP-005 | Air Travel Policy | Class of service, booking, fees, loyalty, companion travel |
| TEP-009 | Employee Grades Reference | Grade ladder + approval authority + acting capacity |
| TEP-013 | International Travel Policy | International approval, risk, FX, visas, dependents |

The other four (policy5–policy8) are realistic noise: Code of Conduct, Records
Retention, Data Classification + IT AUP + Mobile Device Standard, Sustainability.

**TEP-001 cross-references at least 11 other documents that are not in the
corpus:** TEP-002 Meals · TEP-003 Alcohol · TEP-004 Lodging · TEP-006 Ground
Transportation · TEP-007 Receipt Requirements · TEP-008 Per-Diem Rates · TEP-010
Corporate Card · TEP-014 Conference Attendance · TEP-012 Gifts · HR-201
Compensation · IT-405 Backup.

### What this means for the system

The brief is explicit:

> **Citation faithfulness.** If your system says "policy TEP-X says Y," the
> actual quoted clause must support Y. We will spot-check.

> **Honest "I don't know."** A system that refuses to answer or marks an item
> low-confidence when retrieval is weak is worth more than one that confidently
> picks a wrong answer. We will deliberately test this.

The current [deterministic.py](../backend/app/services/deterministic.py) cites
`TEP-002 §2` (meal caps), `TEP-003 §3.1` (alcohol on solo travel), `TEP-004 §3`
(lodging tiers), `TEP-007 §6.2` (receipt mismatch). **None of those clauses
exist in the corpus we were given.** This will fail a spot-check.

### Decision

Treat the missing-policy gap as a **product principle stress test**, not a bug.
The right behavior:

1. **Deterministic rules continue to fire on arithmetic** (over-cap, alcohol
   present, first class, etc.). The amounts are clearly the intended rules —
   they just lack a citation surface in the corpus.
2. **The finding's `policy_doc` field is set to the closest in-corpus anchor**
   (e.g., TEP-001 §3.2 *Reasonableness*) and a clear human-readable note
   explains that the specific numeric threshold "lives in TEP-002, which is not
   present in the provided policy library — treat as a finance-team
   convention pending corpus update."
3. **The adjudicator continues to be constrained** to retrieved clauses only;
   it never invents a citation for missing docs.
4. **The Q&A endpoint refuses** any question whose strongest retrieved clause
   is below the score floor, regardless of how plausible the question is.

This converts a corpus weakness into an *evidence* of the system's honesty.
A reviewer comparing two demos — one that confidently quotes TEP-002 §2 without
TEP-002 existing, and ours that says "the cap is not in the corpus; here's the
reasonableness clause from TEP-001 §3.2 that supports flagging it" — knows
which is trustworthy.

> **Action item (Phase 3):** rewrite the `policy_doc` / `policy_section`
> fields in [deterministic.py](../backend/app/services/deterministic.py) to
> only reference in-corpus clauses (TEP-001, TEP-005, TEP-009, TEP-013).

---

## Submission triage — manual review

Detailed per-submission notes in [submission_review.md](submission_review.md).
The summary:

| # | Submission | Type | Key signals | Needs trip context? |
|---|---|---|---|---|
| 01 | `01_clean_denver` | clean | 8 receipts, all within obvious bounds. | No — pure arithmetic. |
| 02 | `02_clean_boston_conf` | clean | Premium Select on a 6h48m flight — *just barely* allowed by TEP-005 §2.2. Hilton Boston $278/night within Tier 1 cap. Internal-only group dinner. | Slightly — need to know flight duration ≥6h to OK premium. |
| 03 | `03_dinner_over_cap` | violation (flag) | Alinea solo $148.20 — ~2× dinner cap. Note in receipt explicitly says "Solo diner. No external attendees." | Yes — trip is vendor visit, not client entertainment. |
| 04 | `04_alcohol_solo_travel` | violation (reject) | Trip purpose contains "Solo carrier research trip". Franklin dinner contains $26 of beer + wine. | Yes — `solo` in trip purpose is the trigger. |
| 05 | `05_receipt_mismatch` | violation (flag) | Hyatt Seattle $389/night room — over Tier 1 cap. Receipt note: "Booked outside Concur Travel tool; no corporate-rate adjustment applied." | Partial — needs city-tier lookup; "mismatch" turned out to be over-cap + off-tool, not arithmetic mismatch. |

### Ambiguous edge cases worth calling out

- **Tip lines that are negative or near-zero** (Torchy's `-$1.09 counter
  service`, Snooze `$0.03`). These are clearly artifacts of rounding /
  counter-service models, not policy violations. The tip-% check must handle
  `tip ≤ 0` as not a flag.
- **Concur booking source** is mentioned in 02 (Delta) and called out as
  *missing* in 05 (Hyatt). TEP-005 §3.1 applies to flights ("whenever
  practical") — there is no in-corpus lodging clause about Concur. The 05
  Hyatt off-tool booking is a soft signal, not a hard rule.
- **Group dinners** (02 Eastern Standard: 3 NW employees, split check, "no
  external clients"). Per-attendee math should be applied to the *individual*
  ticket, not the table total.
- **Solo-traveler client meal vs personal meal**. Alinea 03 receipt note says
  "Solo diner. No external attendees" — clearly a personal indulgence on a
  vendor trip, not a client dinner. The trip purpose alone is insufficient;
  the receipt's attendee note is the deciding fact.

---

## Golden truth draft

Per-receipt expected verdicts, with rationale, in
[golden_truth.json](golden_truth.json). This is the *manual reviewer's*
answer key — produced from reading every PDF by hand — and is the input to
[../eval/expected_outcomes.json](../eval/expected_outcomes.json) for the
automated harness. Eval expectations should be drawn from this file; if they
disagree, the design document or the rule wins, not whatever the system
happens to output.

Headline counts:

- **31 receipts total** across 5 submissions.
- **27 compliant**.
- **3 flagged** (Alinea dinner over cap; Hyatt Seattle over Tier 1 cap;
  Hyatt Seattle off-tool booking — these last two are the same receipt with
  two findings).
- **1 rejected** (Franklin alcohol on solo travel).
- **0 needs_human_review** *in the golden truth* — but the harness should
  insert at least one synthetic low-confidence case (e.g., a blurred /
  text-stripped receipt) to verify the escalation path actually fires.

# Phase 1 — Design Lock

This document is the **frozen contract** the rest of the system is built against.
Anything that contradicts what is written here is a bug, not a feature.

---

## 1. Core entities

| Entity | Purpose | Key fields | Owns |
|---|---|---|---|
| **Employee** | The person submitting expenses. | `id`, `name`, `email`, `grade`, `home_city`, `manager` | Identity, grade-based eligibility (e.g. business class only for grade ≥ 7). |
| **Submission** | One trip / expense report. A bundle of receipts under one trip purpose. | `id`, `employee_id`, `trip_purpose`, `trip_start`, `trip_end`, `trip_nights`, `destination_city`, `status`, `total_claimed`, `total_reimbursable`, `created_at` | The rollup verdict (`ready_to_approve` / `needs_review` / `has_rejections`) and money totals. |
| **Receipt** | One uploaded file (PDF / image / txt) belonging to a submission. | `id`, `submission_id`, `filename`, `mime_type`, `sha256`, `verdict`, `claimed_amount`, `reimbursable_amount`, `non_reimbursable_amount`, `extracted` (JSON), `findings` (JSON), `adjudication` (JSON), `retrieved_clauses` (JSON), `created_at` | The full audit trail for a single line item. |
| **ExtractedReceiptFields** | Structured JSON the extractor produced from the file. | `merchant`, `transaction_date`, `category`, `subtotal`, `tax`, `tip`, `total`, `currency`, `city`, `alcohol_present`, `alcohol_amount`, `flight_class`, `flight_duration_hours`, `nightly_rate`, `nights`, `attendees`, `claimed_amount`, `extraction_confidence`, `notes` | The model's view of the document. Persisted verbatim so reviewers can see what it "saw". |
| **Verdict** | The final compliance decision per receipt. | `verdict` ∈ taxonomy below, `reasoning`, `confidence`, `quoted_clauses[]` | The reviewer-facing answer. |
| **Override** | A reviewer's deliberate change to a verdict. | `id`, `receipt_id`, `reviewer`, `previous_verdict`, `new_verdict`, `comment` (required), `created_at` | The human-in-the-loop audit trail. Never deletes the original verdict. |
| **PolicyClause** | One numbered section of one policy document, embedded for retrieval. | `id`, `doc_id` (e.g. `TEP-002`), `section` (e.g. `2.1`), `title`, `text`, `family` (e.g. `meals`), `embedding` (Vector(1536)) | The unit of citation. Reviewers see `TEP-002 §2.1`, not "page 4". |
| **RetrievalHit** | One clause returned for one query. | `clause` (PolicyClause), `score` (cosine similarity 0–1), `matched_by` (`vector` / `family` / `keyword`) | Provenance for every quote the LLM is allowed to use. |
| **PolicyQAAnswer** | One Q&A interaction. | `id`, `question`, `answer`, `refused` (bool), `citations[]` (doc_id, section, quote), `created_at` | The "ask the policy library" surface. Refused answers are first-class, not errors. |

**Persistence note**: `findings`, `adjudication`, `retrieved_clauses`, and `extracted`
are stored as JSON columns on `Receipt`. This is deliberate: they are a frozen snapshot
of what the system decided *at the time*, and must not silently change if a clause is
re-embedded or a rule is tweaked.

---

## 2. Final verdict taxonomy

Exactly four verdicts. No others. Every receipt ends in one of these states.

| Verdict | Meaning | Money effect | Who can move it |
|---|---|---|---|
| **`compliant`** | The receipt obeys policy; reimburse the full claimed amount. | `reimbursable = claimed` | Reviewer override only. |
| **`flagged`** | Reimbursable, but a reviewer should look (over cap, high tip, business class, etc.). Soft signal. | `reimbursable = claimed`, with itemized flags shown. | Reviewer can approve / partial-approve / reject. |
| **`rejected`** | Policy forbids reimbursement (first class, solo-travel alcohol, minibar, room upgrade). Hard signal. | `non_reimbursable = claimed` (or the offending line). | Reviewer override **requires** comment and is logged as policy exception. |
| **`needs_human_review`** | The system declines to decide: weak retrieval, low extraction confidence, or conflicting signals. | Held; no money moves. | Reviewer must decide. |

### Invariants

1. **The LLM never overrides a deterministic `reject`.** If any deterministic finding has
   `severity = "reject"`, the verdict is `rejected`, full stop. Adjudicator post-check enforces this.
2. **`compliant` requires zero `flag`/`reject` findings.** A receipt cannot be both `compliant` and flagged.
3. **`needs_human_review` is preferred over a guess.** Triggered by:
   - `extraction_confidence < 0.35` (extractor itself uncertain), **or**
   - top retrieval score < 0.45 on a non-trivial category (no clause supports a decision), **or**
   - LLM adjudicator explicitly chooses it.
4. **Overrides do not mutate the original verdict.** They are appended; the original is preserved for audit.

---

## 3. Confidence meaning

A single confidence number means different things in different layers. We define both
explicitly and use the same bands for both.

| Band | Range | Extraction meaning | Adjudication meaning | System action |
|---|---|---|---|---|
| **High** | 0.85 – 1.00 | All key fields cleanly parsed; vendor, total, date all unambiguous. | Strong retrieval (top score ≥ 0.75) **and** rules + LLM agree. | Verdict applied as-is. |
| **Moderate** | 0.60 – 0.84 | Most fields parsed; one or two were inferred (e.g. category from filename hint). | Retrieval is decent but not crisp, **or** rules and LLM partially disagree on non-reject findings. | Verdict applied; UI shows a "moderate confidence — please skim" hint. |
| **Low** | < 0.60 | Key fields missing or guessed; OCR was noisy. | Weak retrieval (top score < 0.45) **or** LLM unsure. | **Forced** to `needs_human_review`. The system refuses to decide. |

### Specific thresholds wired into code

- **`extraction_confidence < 0.35`** → adjudicator post-check forces `needs_human_review`.
  (This is a stricter floor than the 0.60 band: below 0.35 we don't even trust the structured
  fields enough to *show* a verdict.)
- **`extraction_confidence` in 0.35–0.60** → verdict allowed, but UI surfaces a low-confidence
  warning and the "Show why" panel opens by default.
- **Adjudicator `confidence < 0.60`** → verdict is honored only if it is `needs_human_review`
  or `rejected` (the two "safe" outcomes); `compliant`/`flagged` with sub-0.60 confidence
  is promoted to `needs_human_review`.

Confidence is always **persisted with the receipt**, never derived later. If we
change the bands tomorrow, yesterday's decisions still carry yesterday's number.

---

## 4. Product principle

> **"Never invent a policy answer. If no supporting clause is retrieved, refuse or escalate."**

This is the load-bearing rule of the entire product. Everything else is implementation
detail. Three concrete consequences:

### 4.1 Adjudicator: quotes are constrained to retrieved clauses

The adjudicator prompt is given exactly the clauses returned by the retriever, and the
JSON Schema for its output restricts `quoted_clauses[].doc_id` + `section` to values from
that set. If the model wants to cite something that wasn't retrieved, the structured-output
validator rejects it. There is no path by which the LLM can quote a policy section it did
not actually see.

### 4.2 Policy Q&A: refusal is a first-class answer

The `/qa` endpoint's response schema includes `refused: bool`. The system prompt instructs
the model to set `refused=true` and provide an empty `citations[]` for any question outside
the policy library — vacation requests, payroll questions, "what's the weather", anything.
The eval harness asserts a high refusal rate on a fixed set of out-of-scope trap questions.

When the API key is absent, the offline fallback returns the top retrieved clause
**verbatim** — it cannot fabricate, because it never calls a model.

### 4.3 Verdicts: escalation is always available

Any receipt where the retriever returns nothing useful → `needs_human_review`. Any receipt
where extraction confidence is too low → `needs_human_review`. There is no code path that
produces `compliant` from "I don't know". The deterministic engine, the LLM adjudicator,
and the post-check all have the right to escalate; only the reviewer (with a written
comment) has the right to make a final positive call when the system was uncertain.

---

## Acceptance: how we know we held the line

| Principle | Test in [eval/harness.py](eval/harness.py) |
|---|---|
| Hard rejects are non-negotiable | `04_alcohol_solo_travel` must end `rejected` with TEP-003 §3.1 citation. |
| Caps are deterministic, not LLM-judged | `03_dinner_over_cap` (Alinea) must be `flagged` with TEP-002 §2 citation, exact dollar message. |
| Citations come from retrieval, not memory | `citation_correctness` metric: cited `doc_id` + `section` must appear in the receipt's `retrieved_clauses`. |
| Refusal is real | `refusal_rate` over [eval/oos_questions.json](eval/oos_questions.json) must be ≥ 0.8. |
| Low confidence escalates | A test receipt with `extraction_confidence = 0.2` must end `needs_human_review` regardless of rules. |

If any of these regress, the design is broken — fix the design or the code, but do
not weaken the test.

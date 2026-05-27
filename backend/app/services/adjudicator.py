"""LLM adjudicator: given extracted fields + deterministic findings + retrieved
clauses, produce a verdict with quoted policy support and confidence.

Phase 6 design notes:
    * Retrieval composes a query from category + finding hints + trip context
      and runs a true hybrid (semantic + keyword + clause-type boost) via
      `policy_loader.find_clauses`. The clauses pinned by deterministic
      findings are always promoted to the top.
    * Only the top `MAX_LLM_CLAUSES` (5) clauses are sent to the LLM, and
      only the verbatim `quote` field is shown to the model \u2014 never raw
      embedding text or full documents.
    * The system prompt explicitly forbids citing clauses not in the
      provided list and requires exact-quote support for every claim.
    * A post-validation step strips any `policy_quote` whose quote text
      does not literally appear in any retrieved clause; if validation
      forces removal of supporting evidence, the verdict is downgraded.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..config import get_settings
from ..schemas import (
    Adjudication,
    DeterministicFinding,
    ExtractedReceipt,
    PolicyQuote,
    RetrievedClause,
)
from .deterministic import deterministic_verdict
from .llm import chat_structured
from .policy_loader import find_clauses


log = logging.getLogger(__name__)


# Hard cap on clauses sent to the LLM. The brief explicitly forbids dumping
# full documents; the model gets only the few most relevant clauses.
MAX_LLM_CLAUSES = 5
RETRIEVAL_POOL = 12  # request more then prune


ADJUDICATOR_SYSTEM = """You are a finance compliance adjudicator for Northwind Logistics.

Inputs you receive:
1. A minimal structured summary of the receipt (no raw OCR, no notes).
2. Trip + employee context (purpose, grade, nights, destination).
3. Deterministic findings already computed by a rule engine. Every finding
   carries `policy_refs` that anchor it to specific clauses in the
   `retrieved_clauses` list.
4. `retrieved_clauses` \u2014 a short list of policy clauses. THIS IS THE
   ONLY POLICY TEXT YOU MAY CITE.

Hard constraints:
- Choose ONE verdict: compliant | flagged | rejected | needs_human_review.
- If ANY deterministic finding has severity "reject", the verdict MUST be
  "rejected" or "flagged" (never "compliant").
- Every `policy_quote.quote` MUST be a verbatim substring of one of the
  `retrieved_clauses[*].quote` strings. Never paraphrase, never invent text,
  never quote a doc_id or section that does not appear in retrieved_clauses.
- If you cannot find supporting evidence in retrieved_clauses for a claim,
  remove the claim or choose "needs_human_review" with an `ambiguity_reason`.
- If retrieval looks weak (no clearly-relevant clauses) OR
  extraction_confidence < 0.35, choose "needs_human_review".
- reimbursable_amount + non_reimbursable_amount must equal the receipt total
  when total is known. Never invent dollar amounts.
- Rationale: <= 3 plain-English sentences. No marketing tone, no apologies.
"""


# Routing: pick which policy families to retrieve over for each category.
# The case-study corpus only includes 8 PDFs, so most expense categories
# route into the `travel_overview` family (TEP-001 covers meals, lodging,
# ground transport, and receipt requirements). Air and international have
# their own dedicated docs.
#
# Families present in the corpus after ingestion:
#   travel_overview (TEP-001), air (TEP-005), grades (TEP-009),
#   international (TEP-013), noise_conduct (COC-001),
#   noise_records (REC-001), noise_data_class (SEC-201),
#   noise_sustainability (SUS-001).
#
# `grades` is included on every route so VP/Director approval thresholds
# surface alongside any expense type. `noise_conduct` is included where
# falsification / personal-use concerns can arise.
FAMILY_ROUTE: dict[str, list[str]] = {
    "meal_breakfast": ["travel_overview", "grades", "noise_conduct"],
    "meal_lunch": ["travel_overview", "grades", "noise_conduct"],
    "meal_dinner": ["travel_overview", "grades", "noise_conduct"],
    "meal_other": ["travel_overview", "grades", "noise_conduct"],
    "lodging": ["travel_overview", "international", "grades", "noise_conduct"],
    "air_travel": ["air", "international", "grades", "travel_overview"],
    "ground_rideshare": ["travel_overview", "grades", "noise_conduct"],
    "ground_taxi": ["travel_overview", "grades", "noise_conduct"],
    "ground_parking": ["travel_overview", "grades"],
    "ground_transit": ["travel_overview", "grades"],
    "conference_registration": ["travel_overview", "grades"],
    "entertainment": ["travel_overview", "noise_conduct", "grades"],
    "other": ["travel_overview", "grades", "noise_conduct"],
    "unknown": ["travel_overview", "grades", "noise_conduct"],
}


# ============================================================
# Retrieval
# ============================================================

def _retrieval_query(
    extracted: ExtractedReceipt,
    findings: list[DeterministicFinding],
    trip_purpose: str,
    employee_grade: int,
) -> str:
    """Build a free-text query that fuses category + rule hints + context.

    The query string is what `find_clauses` embeds AND keyword-matches.
    Order is deliberate: most-discriminating tokens first."""
    parts: list[str] = []
    if extracted.category:
        parts.append(extracted.category.replace("_", " "))
    # Rule-engine hints \u2014 the rule_id is the most specific signal.
    for f in findings:
        parts.append(f.rule_id.replace("_", " "))
    if extracted.alcohol_present:
        parts.append("alcohol entertainment")
    if extracted.has_minibar_charge:
        parts.append("minibar")
    if extracted.flight_class:
        parts.append(f"{extracted.flight_class} class flight")
    if extracted.rideshare_tier == "premium":
        parts.append("premium rideshare")
    if extracted.is_international:
        parts.append("international travel")
    if extracted.merchant:
        parts.append(extracted.merchant)
    if trip_purpose:
        parts.append(trip_purpose)
    if employee_grade:
        parts.append(f"grade {employee_grade} approval")
    return " | ".join(p for p in parts if p)


def _keyword_hints(
    extracted: ExtractedReceipt, findings: list[DeterministicFinding]
) -> list[str]:
    """Discrete keyword tokens to feed the keyword-overlap scorer."""
    hints: list[str] = []
    if extracted.category:
        hints.append(extracted.category.split("_")[-1])  # "dinner", "rideshare"...
    for f in findings:
        # rule_ids encode the policy concept (e.g. tip_over_20pct, first_class_prohibited)
        hints.extend(f.rule_id.split("_"))
    if extracted.alcohol_present:
        hints.extend(["alcohol", "entertainment"])
    if extracted.has_minibar_charge:
        hints.append("minibar")
    if extracted.flight_class:
        hints.extend([extracted.flight_class, "flight"])
    if extracted.is_international:
        hints.append("international")
    # de-dupe, drop very short tokens
    seen: set[str] = set()
    out: list[str] = []
    for h in hints:
        h = (h or "").lower().strip()
        if len(h) >= 4 and h not in seen:
            seen.add(h)
            out.append(h)
    return out[:10]


def retrieve(
    db: Session,
    extracted: ExtractedReceipt,
    findings: list[DeterministicFinding],
    trip_purpose: str,
    employee_grade: int = 1,
    k: int = MAX_LLM_CLAUSES,
) -> list[RetrievedClause]:
    """Phase 6 retrieval pipeline.

    1. Build the query + keyword hints from category + rule findings + ctx.
    2. Ask `find_clauses` for `RETRIEVAL_POOL` candidates filtered by the
       category-routed families (hybrid semantic + keyword + clause-type).
    3. **Always promote** clauses whose (doc_id, section) is referenced by
       a deterministic finding \u2014 they are guaranteed-relevant ground truth.
    4. Return top `k` (default 5) `RetrievedClause`s.
    """
    families = FAMILY_ROUTE.get(extracted.category, ["travel_overview", "receipts"])
    query = _retrieval_query(extracted, findings, trip_purpose, employee_grade)
    hints = _keyword_hints(extracted, findings)

    pool = find_clauses(
        db, query, families=families, k=RETRIEVAL_POOL, keywords=hints
    )

    # ----- Pin clauses anchored by deterministic findings ----------------
    # Each finding may reference a specific (doc_id, section). If those exact
    # clauses are not already in the pool, fetch them now \u2014 they are the
    # most reliable evidence the LLM can quote.
    from ..models import PolicyClause

    have: dict[tuple[str, str], PolicyClause] = {(r.doc_id, r.section): r for r in pool}
    pinned: list[PolicyClause] = []
    for f in findings:
        for ref in f.policy_refs:
            # parse "TEP-001 \u00a73.2" -> ("TEP-001", "3.2")
            parts = ref.replace("\u00a7", " ").split()
            if len(parts) < 2:
                continue
            doc_id, section = parts[0], parts[1].rstrip(".")
            key = (doc_id, section)
            if key in have:
                pinned.append(have[key])
                continue
            row = (
                db.query(PolicyClause)
                .filter(
                    PolicyClause.doc_id == doc_id,
                    PolicyClause.section == section,
                )
                .first()
            )
            if row is not None:
                pinned.append(row)
                have[key] = row

    # Build the final ordered list: pinned first (dedup), then pool, cap at k.
    ordered: list[PolicyClause] = []
    seen_keys: set[tuple[str, str]] = set()
    for r in pinned + pool:
        key = (r.doc_id, r.section)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ordered.append(r)
        if len(ordered) >= k:
            break

    out: list[RetrievedClause] = []
    for r in ordered:
        out.append(
            RetrievedClause(
                doc_id=r.doc_id,
                section=r.section,
                clause_title=r.clause_title,
                text=r.text,
                score=1.0,
                clause_type=getattr(r, "clause_type", None),
                keywords=getattr(r, "keywords", None) or [],
                quote=getattr(r, "quote", None) or r.text,
            )
        )
    return out


# ============================================================
# LLM payload construction
# ============================================================

# Receipt fields surfaced to the LLM. We deliberately omit `notes`,
# `raw_text`, `extraction_issues`, `line_items` (verbose) so the prompt
# stays focused on adjudicable signals.
_LLM_EXTRACTED_FIELDS = (
    "merchant", "transaction_date", "currency", "subtotal", "tax", "tip",
    "total", "category", "attendees", "alcohol_present", "alcohol_amount",
    "origin", "destination", "rideshare_tier", "flight_class",
    "flight_duration_hours", "is_international", "hotel_nights",
    "nightly_rate", "city", "has_minibar_charge", "has_room_upgrade",
    "claimed_amount", "extraction_confidence",
)


def _slim_extracted(extracted: ExtractedReceipt) -> dict:
    full = extracted.model_dump(exclude_none=True)
    return {k: v for k, v in full.items() if k in _LLM_EXTRACTED_FIELDS}


def _slim_clauses_for_llm(clauses: list[RetrievedClause]) -> list[dict]:
    """LLM only sees: doc_id, section, clause_title, clause_type, quote.
    No internal text/score/keywords \u2014 keeps the prompt tight and the
    citation surface unambiguous."""
    return [
        {
            "doc_id": c.doc_id,
            "section": c.section,
            "clause_title": c.clause_title,
            "clause_type": c.clause_type,
            "quote": (c.quote or c.text)[:1200],
        }
        for c in clauses
    ]


# ============================================================
# Post-validation: citation faithfulness
# ============================================================

def _validate_quotes(
    adjudication: Adjudication, clauses: list[RetrievedClause]
) -> tuple[Adjudication, list[str]]:
    """Drop any `policy_quote` whose `quote` text is not a literal substring
    of one of the retrieved clause quotes, and any whose (doc_id, section)
    is not in retrieved_clauses. Returns the cleaned adjudication plus a
    list of human-readable validation notes."""
    by_key: dict[tuple[str, str], str] = {
        (c.doc_id, c.section): (c.quote or c.text or "") for c in clauses
    }
    kept: list[PolicyQuote] = []
    notes: list[str] = []
    for pq in adjudication.policy_quotes or []:
        key = (pq.doc_id, pq.section)
        source = by_key.get(key)
        if source is None:
            notes.append(
                f"Dropped quote {pq.doc_id} \u00a7{pq.section}: not in "
                "retrieved_clauses."
            )
            continue
        # Substring match \u2014 tolerate whitespace differences.
        needle = " ".join((pq.quote or "").split())
        haystack = " ".join(source.split())
        if needle and needle in haystack:
            kept.append(pq)
        else:
            notes.append(
                f"Dropped quote {pq.doc_id} \u00a7{pq.section}: text not a "
                "verbatim substring of the retrieved clause."
            )

    adjudication.policy_quotes = kept

    # Drop policy_refs that point to docs we never retrieved.
    retrieved_docs = {c.doc_id for c in clauses}
    cleaned_refs = [r for r in (adjudication.policy_refs or []) if r.split()[0] in retrieved_docs]
    if len(cleaned_refs) != len(adjudication.policy_refs or []):
        notes.append("Dropped policy_refs pointing to non-retrieved docs.")
    adjudication.policy_refs = cleaned_refs

    return adjudication, notes


# ============================================================
# Main entry point
# ============================================================

def adjudicate(
    db: Session,
    extracted: ExtractedReceipt,
    findings: list[DeterministicFinding],
    trip_purpose: str,
    employee_grade: int,
) -> tuple[Adjudication, list[RetrievedClause]]:
    s = get_settings()

    clauses = retrieve(
        db, extracted, findings, trip_purpose, employee_grade=employee_grade
    )
    prelim_verdict, reimb, non_reimb = deterministic_verdict(findings, extracted)

    # Slim payload \u2014 the prompt explicitly tells the model that
    # retrieved_clauses is the ONLY policy text it may cite.
    payload = {
        "extracted_receipt": _slim_extracted(extracted),
        "trip_context": {
            "trip_purpose": trip_purpose,
            "employee_grade": employee_grade,
        },
        "deterministic_findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "message": f.message,
                "policy_refs": f.policy_refs,
                "amount_affected": f.amount_affected,
            }
            for f in findings
        ],
        "retrieved_clauses": _slim_clauses_for_llm(clauses),
        "preliminary": {
            "verdict": prelim_verdict,
            "reimbursable_amount": reimb,
            "non_reimbursable_amount": non_reimb,
        },
    }

    user_parts = [{"type": "text", "text": json.dumps(payload, indent=2)}]

    adjudication: Optional[Adjudication] = None
    if s.llm_enabled:
        adjudication = chat_structured(
            model=s.openai_adjudication_model,
            schema_cls=Adjudication,
            system=ADJUDICATOR_SYSTEM,
            user_parts=user_parts,
            temperature=0.0,
        )

    if adjudication is None:
        adjudication = _adjudicate_offline(
            extracted, findings, clauses, prelim_verdict, reimb, non_reimb
        )

    # ----- Post-validation: citation faithfulness ------------------------
    adjudication, validation_notes = _validate_quotes(adjudication, clauses)
    if validation_notes:
        log.info("Citation validation: %s", "; ".join(validation_notes))
        # If we stripped ALL supporting quotes for a non-compliant verdict,
        # the model has nothing to stand on \u2014 downgrade.
        if (
            adjudication.verdict in {"flagged", "rejected"}
            and not adjudication.policy_quotes
            and findings
        ):
            adjudication.verdict = "needs_human_review"
            adjudication.ambiguity_reason = (
                (adjudication.ambiguity_reason or "")
                + " Adjudicator quotes failed citation validation."
            ).strip()
            adjudication.confidence = min(adjudication.confidence, 0.4)

    # ----- Hard guards ---------------------------------------------------
    # Never override a deterministic reject.
    if any(f.severity == "reject" for f in findings) and adjudication.verdict == "compliant":
        adjudication.verdict = "flagged" if reimb > 0 else "rejected"
        adjudication.ambiguity_reason = (
            (adjudication.ambiguity_reason or "")
            + " Deterministic reject override applied."
        ).strip()

    # Confidence gate: very low extraction confidence \u2192 needs_human_review.
    if extracted.extraction_confidence < 0.35 and adjudication.verdict == "compliant":
        adjudication.verdict = "needs_human_review"
        adjudication.ambiguity_reason = (
            (adjudication.ambiguity_reason or "")
            + " Extraction confidence below 0.35; reviewer should verify fields."
        ).strip()
        adjudication.confidence = min(adjudication.confidence, 0.4)

    return adjudication, clauses


def _adjudicate_offline(
    extracted: ExtractedReceipt,
    findings: list[DeterministicFinding],
    clauses: list[RetrievedClause],
    prelim_verdict: str,
    reimb: float,
    non_reimb: float,
) -> Adjudication:
    """Build an Adjudication purely from rules + retrieved clauses, used when
    OPENAI_API_KEY is not set. Honest about its provenance."""
    refs = sorted({r for f in findings for r in f.policy_refs})
    quotes: list[PolicyQuote] = []
    seen: set[tuple[str, str]] = set()
    for f in findings:
        for ref in f.policy_refs:
            for c in clauses:
                key = (c.doc_id, c.section)
                if key in seen:
                    continue
                if c.doc_id in ref and c.section in ref:
                    quotes.append(
                        PolicyQuote(
                            doc_id=c.doc_id,
                            section=c.section,
                            quote=(c.quote or c.text)[:600],
                        )
                    )
                    seen.add(key)
                    break
    if not findings and not clauses:
        return Adjudication(
            verdict="needs_human_review",
            rationale="LLM disabled and no rules triggered; reviewer should confirm.",
            confidence=0.3,
            ambiguity_reason="Offline mode, no signal.",
            reimbursable_amount=extracted.total or 0.0,
            non_reimbursable_amount=0.0,
        )
    rationale = (
        "Deterministic rule engine produced this verdict (LLM disabled). "
        + " ".join(f.message for f in findings)[:600]
    )
    return Adjudication(
        verdict=prelim_verdict,
        rationale=rationale or "No issues detected by deterministic rules.",
        policy_quotes=quotes,
        policy_refs=refs,
        reimbursable_amount=reimb,
        non_reimbursable_amount=non_reimb,
        confidence=0.7 if findings else 0.5,
        ambiguity_reason=None if findings else "No rule triggered; manual review recommended.",
        recommended_reviewer_action=None,
    )

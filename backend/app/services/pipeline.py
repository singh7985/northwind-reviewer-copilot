"""High-level pipeline: from an uploaded file to a persisted, adjudicated receipt."""
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Receipt, Submission
from ..schemas import (
    Adjudication,
    DeterministicFinding,
    ExtractedReceipt,
    PolicyQuote,
)
from .adjudicator import adjudicate
from .deterministic import (
    check_submission_approval_threshold,
    classify_category,
    run_rules,
)
from .extraction import extract_receipt


log = logging.getLogger(__name__)


def _nights(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    from datetime import date

    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        return max((e - s).days, 1)
    except Exception:
        return None


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def process_receipt(
    db: Session,
    submission: Submission,
    file_path: str,
    filename: str,
    mime_type: str,
) -> Receipt:
    # ---- 1. Fingerprint the file before anything expensive happens -----
    sha = _sha256_of(file_path)
    dup = next(
        (r for r in submission.receipts if r.sha256 == sha),
        None,
    )

    # ---- 2. Extract (always; even duplicates get their own audit row) --
    extracted = extract_receipt(file_path, mime_type)
    # Defensive classifier: confirm or repair the LLM-supplied category
    # using deterministic merchant/filename hints.
    extracted.category = classify_category(extracted, filename=filename)
    issues = list(extracted.extraction_issues or [])
    if dup is not None:
        issues.append("duplicate_in_submission")

    # ---- 3. Decide whether to run the deterministic + LLM pipeline -----
    # Short-circuit cases that should never be auto-decided:
    #   - unreadable file: nothing to judge.
    #   - duplicate-in-submission: human must decide whether to drop one.
    short_circuit = (
        "unreadable" in issues or "duplicate_in_submission" in issues
    )

    if short_circuit:
        findings: list[DeterministicFinding] = []
        if "unreadable" in issues:
            findings.append(
                DeterministicFinding(
                    rule_id="unreadable_receipt",
                    severity="flag",
                    message=(
                        "Receipt could not be read (no text trace, no "
                        "usable image); itemized documentation is required "
                        "by TEP-001 §3.3."
                    ),
                    policy_refs=["TEP-001 §3.3"],
                )
            )
        if "duplicate_in_submission" in issues:
            findings.append(
                DeterministicFinding(
                    rule_id="duplicate_in_submission",
                    severity="flag",
                    message=(
                        f"File hash matches an existing receipt in this submission "
                        f"({dup.filename if dup else 'unknown'}). Duplicate claims "
                        "are prohibited by COC-001 §2.2 (no falsification of "
                        "expense reports); see also TEP-001 §3.3."
                    ),
                    policy_refs=["COC-001 §2.2", "TEP-001 §3.3"],
                )
            )
        adjudication = Adjudication(
            verdict="needs_human_review",
            rationale=(
                "Receipt escalated without verdict — see extraction_issues. "
                "A reviewer must confirm before any reimbursement decision "
                "(TEP-001 §3.3)."
            ),
            policy_quotes=[],
            policy_refs=["TEP-001 §3.3"],
            reimbursable_amount=0.0,
            non_reimbursable_amount=0.0,
            confidence=0.0,
            ambiguity_reason="; ".join(issues),
            recommended_reviewer_action=(
                "Re-upload a legible copy"
                if "unreadable" in issues
                else "Confirm whether this duplicates an existing receipt and delete the extra."
            ),
        )
        clauses = []
    else:
        # Build sibling-receipts list for cross-receipt rules
        # (e.g. conference_meal_overlap).
        siblings: list[ExtractedReceipt] = []
        for r in submission.receipts:
            try:
                siblings.append(ExtractedReceipt.model_validate(r.extracted or {}))
            except Exception:
                continue
        findings = run_rules(
            extracted,
            trip_purpose=submission.trip_purpose,
            trip_nights=_nights(submission.trip_start, submission.trip_end),
            employee_grade=submission.employee.grade,
            sibling_receipts=siblings,
        )
        adjudication, clauses = adjudicate(
            db,
            extracted,
            findings,
            trip_purpose=submission.trip_purpose,
            employee_grade=submission.employee.grade,
        )

    # ---- 4. Persist the full audit row --------------------------------
    rec = Receipt(
        id=str(uuid.uuid4()),
        submission_id=submission.id,
        filename=filename,
        mime_type=mime_type,
        storage_path=file_path,
        sha256=sha,
        extracted=extracted.model_dump(mode="json"),
        raw_text=extracted.raw_text,
        extraction_confidence=extracted.extraction_confidence,
        extraction_issues=issues,
        deterministic_findings=[f.model_dump() for f in findings],
        retrieved_clauses=[c.model_dump() for c in clauses],
        verdict=adjudication.verdict,
        rationale=adjudication.rationale,
        policy_quotes=[q.model_dump() for q in adjudication.policy_quotes],
        policy_refs=adjudication.policy_refs,
        reimbursable_amount=adjudication.reimbursable_amount,
        non_reimbursable_amount=adjudication.non_reimbursable_amount,
        confidence=adjudication.confidence,
        ambiguity_reason=adjudication.ambiguity_reason,
        recommended_reviewer_action=adjudication.recommended_reviewer_action,
        # Immutable trace: original_* must equal the system's answer at
        # adjudication time. Overrides mutate `verdict`/`rationale`/etc.
        # but never these fields, so a reviewer can always see what the
        # copilot first said.
        original_verdict=adjudication.verdict,
        original_rationale=adjudication.rationale,
        original_reimbursable_amount=adjudication.reimbursable_amount,
        original_non_reimbursable_amount=adjudication.non_reimbursable_amount,
        original_confidence=adjudication.confidence,
    )
    db.add(rec)
    db.flush()
    _recompute_submission_totals(db, submission)
    return rec


def _recompute_submission_totals(db: Session, submission: Submission) -> None:
    submission.total_claimed = sum(
        (r.extracted or {}).get("total") or 0.0 for r in submission.receipts
    )
    submission.total_reimbursable = sum(r.reimbursable_amount for r in submission.receipts)
    submission.total_non_reimbursable = sum(
        r.non_reimbursable_amount for r in submission.receipts
    )
    counts: dict[str, int] = {"compliant": 0, "flagged": 0, "rejected": 0, "needs_human_review": 0}
    for r in submission.receipts:
        counts[r.verdict] = counts.get(r.verdict, 0) + 1
    submission.counts = counts
    submission.status = _rollup_status(counts)

    # Submission-level approval rule (TEP-001 §4.2). Stored as a list so the
    # UI/eval can iterate even when more submission-scoped rules are added.
    threshold = check_submission_approval_threshold(
        submission.total_claimed,
        employee_grade=submission.employee.grade,
        receipt_count=len(submission.receipts),
    )
    finding = threshold.to_finding()
    submission.submission_findings = [finding.model_dump()] if finding else []


def _rollup_status(counts: dict[str, int]) -> str:
    if counts.get("rejected", 0):
        return "has_rejections"
    if counts.get("flagged", 0) or counts.get("needs_human_review", 0):
        return "needs_review"
    return "ready_to_approve"

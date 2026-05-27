from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Employee, Receipt, Submission
from ..schemas import ReceiptOut, SubmissionCreate, SubmissionOut
from ..services.pipeline import process_receipt


router = APIRouter(prefix="/submissions", tags=["submissions"])


def _to_receipt_out(r: Receipt) -> ReceiptOut:
    return ReceiptOut(
        id=r.id,
        submission_id=r.submission_id,
        filename=r.filename,
        mime_type=r.mime_type,
        extracted=r.extracted,
        extraction_confidence=r.extraction_confidence,
        extraction_issues=r.extraction_issues or [],
        deterministic_findings=r.deterministic_findings,
        retrieved_clauses=r.retrieved_clauses,
        verdict=r.verdict,
        rationale=r.rationale,
        policy_quotes=r.policy_quotes,
        policy_refs=r.policy_refs,
        reimbursable_amount=r.reimbursable_amount,
        non_reimbursable_amount=r.non_reimbursable_amount,
        confidence=r.confidence,
        ambiguity_reason=r.ambiguity_reason,
        recommended_reviewer_action=r.recommended_reviewer_action,
        overrides=[
            {
                "id": o.id,
                "reviewer": o.reviewer,
                "previous_verdict": o.previous_verdict,
                "new_verdict": o.new_verdict,
                "comment": o.comment,
                "created_at": o.created_at.isoformat(),
            }
            for o in r.overrides
        ],
        created_at=r.created_at,
        original_verdict=r.original_verdict,
        original_rationale=r.original_rationale,
        original_reimbursable_amount=r.original_reimbursable_amount,
        original_non_reimbursable_amount=r.original_non_reimbursable_amount,
        original_confidence=r.original_confidence,
    )


def _to_submission_out(s: Submission) -> SubmissionOut:
    return SubmissionOut(
        id=s.id,
        employee_id=s.employee_id,
        employee_name=s.employee.name if s.employee else None,
        trip_purpose=s.trip_purpose,
        trip_start=s.trip_start,
        trip_end=s.trip_end,
        destination=s.destination,
        status=s.status,
        total_claimed=s.total_claimed,
        total_reimbursable=s.total_reimbursable,
        total_non_reimbursable=s.total_non_reimbursable,
        counts=s.counts or {},
        submission_findings=s.submission_findings or [],
        receipts=[_to_receipt_out(r) for r in s.receipts],
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.post("", response_model=SubmissionOut)
def create_submission(payload: SubmissionCreate, db: Session = Depends(get_db)):
    emp = db.get(Employee, payload.employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    s = Submission(
        id=str(uuid.uuid4()),
        employee_id=payload.employee_id,
        trip_purpose=payload.trip_purpose,
        trip_start=payload.trip_start,
        trip_end=payload.trip_end,
        destination=payload.destination,
        status="pending",
        counts={},
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_submission_out(s)


@router.get("", response_model=list[SubmissionOut])
def list_submissions(
    employee_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD inclusive"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD inclusive"),
    db: Session = Depends(get_db),
):
    from datetime import datetime, timedelta

    q = db.query(Submission)
    if employee_id:
        q = q.filter(Submission.employee_id == employee_id)
    if status:
        q = q.filter(Submission.status == status)
    if date_from:
        try:
            dt = datetime.strptime(date_from, "%Y-%m-%d")
            q = q.filter(Submission.created_at >= dt)
        except ValueError:
            raise HTTPException(400, "date_from must be YYYY-MM-DD")
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            q = q.filter(Submission.created_at < dt)
        except ValueError:
            raise HTTPException(400, "date_to must be YYYY-MM-DD")
    rows = q.order_by(Submission.created_at.desc()).all()
    return [_to_submission_out(r) for r in rows]


@router.get("/{submission_id}", response_model=SubmissionOut)
def get_submission(submission_id: str, db: Session = Depends(get_db)):
    s = db.get(Submission, submission_id)
    if not s:
        raise HTTPException(404, "Submission not found")
    return _to_submission_out(s)


@router.post("/{submission_id}/receipts", response_model=ReceiptOut)
async def upload_receipt(
    submission_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    s = db.get(Submission, submission_id)
    if not s:
        raise HTTPException(404, "Submission not found")
    settings = get_settings()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename or 'upload').name}"
    dest = Path(settings.upload_dir) / safe_name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    mime = file.content_type or "application/octet-stream"
    rec = process_receipt(db, s, str(dest), file.filename or safe_name, mime)
    db.commit()
    db.refresh(rec)
    db.refresh(s)
    return _to_receipt_out(rec)


@router.post("/seed_from_disk/{submission_dir}", response_model=SubmissionOut)
def seed_submission_from_disk(submission_dir: str, db: Session = Depends(get_db)):
    """Helper for testing/demo: process one of the bundled case-study folders."""
    import json as _json

    s_settings = get_settings()
    base = Path(s_settings.submissions_dir) / submission_dir
    if not base.exists():
        raise HTTPException(404, f"No such submissions dir: {submission_dir}")
    info = _json.loads((base / "employee_info.json").read_text())
    emp = db.get(Employee, info["employee_id"])
    if not emp:
        raise HTTPException(400, "Employee not seeded; run /admin/seed first")
    start, end = None, None
    dates = info.get("trip_dates", "")
    if " to " in dates:
        start, end = dates.split(" to ")
    sub = Submission(
        id=str(uuid.uuid4()),
        employee_id=emp.id,
        trip_purpose=info.get("trip_purpose", ""),
        trip_start=start,
        trip_end=end,
        destination=None,
        status="pending",
        counts={},
    )
    db.add(sub)
    db.flush()
    receipts_dir = base / "receipts"
    for f in sorted(receipts_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".txt"}:
            mime = (
                "application/pdf" if f.suffix.lower() == ".pdf"
                else f"image/{f.suffix.lower().strip('.')}" if f.suffix.lower() in {".png", ".jpg", ".jpeg"}
                else "text/plain"
            )
            process_receipt(db, sub, str(f), f.name, mime)
    db.commit()
    db.refresh(sub)
    return _to_submission_out(sub)

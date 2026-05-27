from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Override, Receipt
from ..schemas import OverrideIn, OverrideOut


router = APIRouter(prefix="/receipts", tags=["overrides"])


@router.post("/{receipt_id}/override", response_model=OverrideOut)
def create_override(receipt_id: str, payload: OverrideIn, db: Session = Depends(get_db)):
    rec = db.get(Receipt, receipt_id)
    if not rec:
        raise HTTPException(404, "Receipt not found")
    ov = Override(
        receipt_id=receipt_id,
        reviewer=payload.reviewer,
        previous_verdict=rec.verdict,
        new_verdict=payload.new_verdict,
        comment=payload.comment,
    )
    rec.verdict = payload.new_verdict
    db.add(ov)
    db.commit()
    db.refresh(ov)
    return ov


@router.get("/{receipt_id}/overrides", response_model=list[OverrideOut])
def list_overrides(receipt_id: str, db: Session = Depends(get_db)):
    rec = db.get(Receipt, receipt_id)
    if not rec:
        raise HTTPException(404, "Receipt not found")
    return rec.overrides

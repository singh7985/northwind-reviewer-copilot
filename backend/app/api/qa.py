from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import PolicyQA
from ..schemas import QAIn, QAOut
from ..services.qa import ask


router = APIRouter(prefix="/qa", tags=["qa"])


@router.post("", response_model=QAOut)
def qa(payload: QAIn, db: Session = Depends(get_db)):
    out = ask(db, payload.question)
    db.add(
        PolicyQA(
            question=payload.question,
            answer=out.answer,
            refused=out.refused,
            citations=out.citations,
        )
    )
    db.commit()
    return out


@router.get("/history")
def history(db: Session = Depends(get_db), limit: int = 25):
    rows = db.query(PolicyQA).order_by(PolicyQA.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "question": r.question,
            "answer": r.answer,
            "refused": r.refused,
            "citations": r.citations,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]

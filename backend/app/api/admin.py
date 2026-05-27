from __future__ import annotations

from fastapi import APIRouter

from ..db import init_db
from ..services.policy_loader import ingest_policies
from ..services.seed import seed_employees


router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/init")
def init():
    init_db()
    return {"ok": True}


@router.post("/seed")
def seed():
    init_db()
    n_emp = seed_employees()
    n_clauses = ingest_policies()
    return {"employees": n_emp, "policy_clauses": n_clauses}

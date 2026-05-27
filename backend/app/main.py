"""FastAPI app entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .api import admin, employees, overrides, qa, submissions


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("northwind")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap persistence on startup so a fresh container is usable.

    Each step is idempotent and best-effort: a failure (e.g. Postgres not
    yet ready in dev) is logged but does not prevent the API from coming
    up — `/admin/seed` and `/admin/init` remain available as fallbacks.
    """
    from .db import init_db, session_scope
    from .models import PolicyClause
    from .services.policy_loader import ingest_policies
    from .services.seed import seed_employees

    try:
        init_db()
        log.info("startup: schema ensured")
    except Exception as e:  # pragma: no cover
        log.warning("startup: init_db failed: %s", e)

    # Lightweight in-place "migration" for additive columns. `create_all`
    # only creates missing tables, never alters existing ones, so columns
    # added after the first deploy need this explicit step. Each clause is
    # idempotent thanks to `ADD COLUMN IF NOT EXISTS`.
    try:
        from sqlalchemy import text as _text
        from .db import engine as _engine

        _ALTERS = [
            "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS original_verdict varchar(32)",
            "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS original_rationale text",
            "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS original_reimbursable_amount double precision",
            "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS original_non_reimbursable_amount double precision",
            "ALTER TABLE receipts ADD COLUMN IF NOT EXISTS original_confidence double precision",
        ]
        with _engine.begin() as conn:
            for ddl in _ALTERS:
                conn.execute(_text(ddl))
        log.info("startup: trace columns ensured")
    except Exception as e:  # pragma: no cover
        log.warning("startup: trace-column migration failed: %s", e)

    # Always seed employees — cheap upsert, guarantees the 5 case-study
    # employees exist after every restart.
    try:
        n = seed_employees()
        log.info("startup: seeded %d employees", n)
    except Exception as e:  # pragma: no cover
        log.warning("startup: seed_employees failed: %s", e)

    # Ingest policies only when the clause table is empty — re-embedding
    # 400+ clauses on every container restart is wasteful.
    try:
        with session_scope() as db:
            existing = db.query(PolicyClause).count()
        if existing == 0:
            n = ingest_policies()
            log.info("startup: ingested %d policy clauses", n)
        else:
            log.info("startup: policy table already populated (%d clauses)", existing)
    except Exception as e:  # pragma: no cover
        log.warning("startup: ingest_policies failed: %s", e)

    yield
    # No shutdown work needed: SQLAlchemy engine handles its own teardown.


app = FastAPI(
    title="Northwind Expense Pre-Review",
    version="0.1.0",
    description="Hybrid (deterministic + LLM) compliance copilot for finance reviewers.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "ok": True,
        "llm_enabled": settings.llm_enabled,
        "extraction_model": settings.openai_extraction_model if settings.llm_enabled else None,
        "adjudication_model": settings.openai_adjudication_model if settings.llm_enabled else None,
    }


app.include_router(admin.router)
app.include_router(employees.router)
app.include_router(submissions.router)
app.include_router(overrides.router)
app.include_router(qa.router)

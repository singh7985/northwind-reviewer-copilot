"""SQLAlchemy ORM models for Northwind expense pre-review system."""
from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


EMBED_DIM = 1536  # text-embedding-3-small


class Base(DeclarativeBase):
    pass


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # employee_id
    name: Mapped[str] = mapped_column(String(255))
    grade: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    department: Mapped[str] = mapped_column(String(255))
    manager_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    home_base: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    submissions: Mapped[list["Submission"]] = relationship(back_populates="employee")


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"))
    trip_purpose: Mapped[str] = mapped_column(Text)
    trip_start: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trip_end: Mapped[str | None] = mapped_column(String(32), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # rolled-up totals
    total_claimed: Mapped[float] = mapped_column(Float, default=0.0)
    total_reimbursable: Mapped[float] = mapped_column(Float, default=0.0)
    total_non_reimbursable: Mapped[float] = mapped_column(Float, default=0.0)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    # Submission-scoped deterministic findings (e.g. VP-approval threshold).
    # List of DeterministicFinding-shaped dicts.
    submission_findings: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    employee: Mapped[Employee] = relationship(back_populates="submissions")
    receipts: Mapped[list["Receipt"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )


class Receipt(Base):
    """One receipt = one line item (per the case-study brief)."""

    __tablename__ = "receipts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE")
    )
    filename: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128))
    storage_path: Mapped[str] = mapped_column(String(1024))

    # extracted structured fields (Pydantic ExtractedReceipt as dict)
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # SHA-256 of the raw bytes — used for duplicate detection within a
    # submission and across the system. Indexed for fast lookup.
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # Stable issue codes from extraction (unreadable, missing_itemization,
    # partial, low_quality, duplicate_in_submission).
    extraction_issues: Mapped[list] = mapped_column(JSON, default=list)

    # deterministic findings
    deterministic_findings: Mapped[list] = mapped_column(JSON, default=list)

    # retrieval
    retrieved_clauses: Mapped[list] = mapped_column(JSON, default=list)

    # adjudicated verdict
    verdict: Mapped[str] = mapped_column(String(32), default="pending")
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_quotes: Mapped[list] = mapped_column(JSON, default=list)
    policy_refs: Mapped[list] = mapped_column(JSON, default=list)
    reimbursable_amount: Mapped[float] = mapped_column(Float, default=0.0)
    non_reimbursable_amount: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ambiguity_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_reviewer_action: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- Immutable adjudication trace ---------------------------------
    # These mirror verdict/rationale/etc. but are written ONCE at
    # adjudication time and never mutated by reviewer overrides. The brief
    # requires that after a restart we can still surface the system's
    # original answer alongside any human overrides.
    original_verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    original_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_reimbursable_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_non_reimbursable_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    submission: Mapped[Submission] = relationship(back_populates="receipts")
    overrides: Mapped[list["Override"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan"
    )


class Override(Base):
    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    receipt_id: Mapped[str] = mapped_column(
        ForeignKey("receipts.id", ondelete="CASCADE")
    )
    reviewer: Mapped[str] = mapped_column(String(255))
    previous_verdict: Mapped[str] = mapped_column(String(32))
    new_verdict: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    receipt: Mapped[Receipt] = relationship(back_populates="overrides")


class PolicyClause(Base):
    """A leaf-level clause from a policy document."""

    __tablename__ = "policy_clauses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(32), index=True)  # e.g. TEP-002
    doc_title: Mapped[str] = mapped_column(String(512))
    doc_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    section: Mapped[str] = mapped_column(String(32), index=True)  # "2.3"
    clause_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    # Verbatim quote preserving original line breaks for citation surface.
    # `text` is whitespace-normalised for embedding/search; `quote` is what
    # the UI shows the reviewer.
    quote: Mapped[str] = mapped_column(Text, default="")
    policy_family: Mapped[str] = mapped_column(String(64), index=True)
    # hard_rule | guidance | exception | definition | informational
    clause_type: Mapped[str] = mapped_column(String(32), index=True, default="informational")
    # Extracted keywords for keyword-fallback retrieval and UI badges.
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    effective_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBED_DIM), nullable=True
    )


class PolicyQA(Base):
    __tablename__ = "policy_qa_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

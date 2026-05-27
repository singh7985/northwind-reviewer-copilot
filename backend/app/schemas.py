"""Pydantic schemas for API + LLM structured outputs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------- Receipt extraction ----------------------

CategoryLiteral = Literal[
    "meal_breakfast",
    "meal_lunch",
    "meal_dinner",
    "meal_other",
    "lodging",
    "air_travel",
    "ground_rideshare",
    "ground_taxi",
    "ground_rental_car",
    "ground_parking",
    "ground_transit",
    "ground_mileage",
    "conference_registration",
    "entertainment",
    "other",
    "unknown",
]


class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    is_alcohol: Optional[bool] = None


class ExtractedReceipt(BaseModel):
    """Structured-output schema for an extracted receipt.

    LLM is instructed to populate fields it can read and to use null /
    empty when unsure. Field-level confidence helps the gate decide
    whether to call human review.
    """

    merchant: Optional[str] = None
    transaction_date: Optional[str] = None  # YYYY-MM-DD
    currency: Optional[str] = "USD"
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    tip: Optional[float] = None
    total: Optional[float] = None
    category: CategoryLiteral = "unknown"
    line_items: list[LineItem] = Field(default_factory=list)
    attendees: list[str] = Field(default_factory=list)
    alcohol_present: Optional[bool] = None
    alcohol_amount: Optional[float] = None

    # transportation
    origin: Optional[str] = None
    destination: Optional[str] = None
    rideshare_tier: Optional[str] = None  # "standard" | "premium" | None
    flight_class: Optional[str] = None  # "economy" | "premium_economy" | "business" | "first"
    flight_duration_hours: Optional[float] = None
    is_international: Optional[bool] = None

    # lodging
    hotel_nights: Optional[int] = None
    nightly_rate: Optional[float] = None
    city: Optional[str] = None
    has_minibar_charge: Optional[bool] = None
    has_room_upgrade: Optional[bool] = None

    # generic
    claimed_amount: Optional[float] = None  # what employee asserts
    notes: Optional[str] = None

    confidence: dict[str, float] = Field(default_factory=dict)
    extraction_confidence: float = 0.5

    # Audit trail (populated by the extraction service, never by the LLM).
    # `raw_text` is the full text trace from pypdf / .txt read; it is what
    # the heuristic fallback parses and what reviewers can spot-check.
    # `extraction_issues` is a list of stable codes:
    #   - "unreadable"           no usable text and no successful vision call
    #   - "missing_itemization"  total present but no subtotal AND no line items
    #   - "partial"              one or more category-required fields missing
    #   - "low_quality"          extraction_confidence < 0.35 floor
    raw_text: Optional[str] = None
    extraction_issues: list[str] = Field(default_factory=list)


# ---------------------- Deterministic findings ----------------------

class DeterministicFinding(BaseModel):
    rule_id: str
    severity: Literal["info", "flag", "reject"]
    message: str
    policy_refs: list[str] = Field(default_factory=list)
    deterministic_quote: Optional[str] = None
    amount_affected: Optional[float] = None


# ---------------------- Retrieval ----------------------

class RetrievedClause(BaseModel):
    doc_id: str
    section: str
    clause_title: Optional[str] = None
    text: str
    score: float
    clause_type: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    quote: Optional[str] = None


# ---------------------- Adjudication ----------------------

VerdictLiteral = Literal["compliant", "flagged", "rejected", "needs_human_review"]


class PolicyQuote(BaseModel):
    doc_id: str
    section: str
    quote: str


class Adjudication(BaseModel):
    """Strict output schema we force the adjudicator LLM into."""

    verdict: VerdictLiteral
    rationale: str
    policy_quotes: list[PolicyQuote] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    reimbursable_amount: float = 0.0
    non_reimbursable_amount: float = 0.0
    confidence: float = 0.5
    ambiguity_reason: Optional[str] = None
    recommended_reviewer_action: Optional[str] = None


# ---------------------- API request/response ----------------------

class EmployeeIn(BaseModel):
    id: str
    name: str
    grade: int
    title: str
    department: str
    manager_id: Optional[str] = None
    home_base: str


class EmployeeOut(EmployeeIn):
    model_config = ConfigDict(from_attributes=True)


class SubmissionCreate(BaseModel):
    employee_id: str
    trip_purpose: str
    trip_start: Optional[str] = None
    trip_end: Optional[str] = None
    destination: Optional[str] = None


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    submission_id: str
    filename: str
    mime_type: str
    extracted: dict
    extraction_confidence: float
    extraction_issues: list[str] = Field(default_factory=list)
    deterministic_findings: list
    retrieved_clauses: list
    verdict: str
    rationale: Optional[str]
    policy_quotes: list
    policy_refs: list
    reimbursable_amount: float
    non_reimbursable_amount: float
    confidence: float
    ambiguity_reason: Optional[str]
    recommended_reviewer_action: Optional[str]
    overrides: list[dict] = Field(default_factory=list)
    created_at: datetime

    # Immutable trace: the verdict the system originally produced. Stays
    # constant across any number of reviewer overrides, so the UI/eval
    # can always show the copilot's first answer next to the current one.
    original_verdict: Optional[str] = None
    original_rationale: Optional[str] = None
    original_reimbursable_amount: Optional[float] = None
    original_non_reimbursable_amount: Optional[float] = None
    original_confidence: Optional[float] = None


class SubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    employee_id: str
    employee_name: Optional[str] = None
    trip_purpose: str
    trip_start: Optional[str]
    trip_end: Optional[str]
    destination: Optional[str]
    status: str
    total_claimed: float
    total_reimbursable: float
    total_non_reimbursable: float
    counts: dict
    submission_findings: list = Field(default_factory=list)
    receipts: list[ReceiptOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class OverrideIn(BaseModel):
    reviewer: str
    new_verdict: VerdictLiteral
    comment: str = Field(min_length=3)


class OverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    receipt_id: str
    reviewer: str
    previous_verdict: str
    new_verdict: str
    comment: str
    created_at: datetime


class QAIn(BaseModel):
    question: str = Field(min_length=2)


class QAOut(BaseModel):
    answer: str
    refused: bool
    citations: list[dict] = Field(default_factory=list)


class EvalCase(BaseModel):
    submission_dir: str
    expected_verdicts: dict[str, str] = Field(default_factory=dict)  # filename -> verdict
    expected_categories: dict[str, str] = Field(default_factory=dict)
    expected_refs: dict[str, list[str]] = Field(default_factory=dict)  # filename -> [refs]


class EvalReport(BaseModel):
    cases: int
    receipts: int
    verdict_accuracy: float
    category_accuracy: float
    citation_correctness: float
    refusal_rate_oos: Optional[float] = None
    details: list[dict] = Field(default_factory=list)

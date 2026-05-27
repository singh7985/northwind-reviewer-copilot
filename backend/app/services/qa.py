"""Policy Q&A: retrieval + grounded answer with explicit refusal for out-of-scope."""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..config import get_settings
from ..schemas import QAOut
from .llm import chat_structured
from .policy_loader import find_clauses
from pydantic import BaseModel, Field


log = logging.getLogger(__name__)


class _QAModel(BaseModel):
    refused: bool
    answer: str
    citations: list[dict] = Field(default_factory=list)


QA_SYSTEM = """You answer questions strictly about Northwind Logistics' internal policy library.

Hard rules:
- If the question is not about Northwind policies (e.g., world trivia, code help, current events,
  the user's personal life), set refused=true and answer with a single short sentence explaining
  you only answer policy questions. Citations must be empty.
- If retrieval returned no relevant clauses for a policy question, set refused=true and explain
  that you couldn't find supporting policy text.
- Otherwise, answer in 2–4 sentences using only the retrieved clauses. Every factual claim must
  be backed by a citation drawn from the retrieved clauses. Quote exact clause text.

Output JSON schema is enforced.
"""


def ask(db: Session, question: str) -> QAOut:
    s = get_settings()
    clauses = find_clauses(db, question, families=None, k=6)
    retrieved = [
        {
            "doc_id": c.doc_id,
            "section": c.section,
            "clause_title": c.clause_title,
            "text": c.text,
        }
        for c in clauses
    ]

    # Heuristic refusal: if best retrieval score looks weak, allow LLM to refuse
    if s.llm_enabled:
        payload = {"question": question, "retrieved_clauses": retrieved}
        result = chat_structured(
            model=s.openai_qa_model,
            schema_cls=_QAModel,
            system=QA_SYSTEM,
            user_parts=[{"type": "text", "text": json.dumps(payload)}],
            temperature=0.0,
        )
        if result is not None:
            return QAOut(
                answer=result.answer, refused=result.refused, citations=result.citations
            )

    # Offline / failure fallback: deterministic refusal + cite top clause if any
    if not retrieved:
        return QAOut(
            answer="I couldn't find policy text relevant to that question.",
            refused=True,
            citations=[],
        )
    top = retrieved[0]
    return QAOut(
        answer=(
            f"LLM disabled. Closest matching clause: {top['doc_id']} §{top['section']}: "
            f"{top['text'][:600]}"
        ),
        refused=False,
        citations=[{"doc_id": c["doc_id"], "section": c["section"], "quote": c["text"][:400]} for c in retrieved[:3]],
    )

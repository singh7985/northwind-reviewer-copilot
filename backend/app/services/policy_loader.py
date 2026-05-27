"""Parse policy PDFs into clause-level rows and (optionally) embed them.

Strategy:
- Read each PDF in policies/ with pypdf.
- Split into sections using regex on numbered headings (e.g. "2.3" / "10.").
- A "clause" = the smallest numbered leaf (or the section text if no sub-numbers).
- Detect doc_id (e.g. TEP-002) and policy_family from the header line.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable

import pypdf

from ..config import get_settings
from ..db import session_scope
from ..models import PolicyClause
from .llm import embed


log = logging.getLogger(__name__)


# doc_id -> policy_family mapping (covers the case-study set)
FAMILY_BY_DOC: dict[str, str] = {
    "TEP-001": "travel_overview",
    "TEP-002": "meals",
    "TEP-003": "alcohol",
    "TEP-004": "lodging",
    "TEP-005": "air",
    "TEP-006": "ground",
    "TEP-007": "receipts",
    "TEP-008": "per_diem",
    "TEP-009": "grades",
    "TEP-010": "corporate_card",
    "TEP-012": "gifts",
    "TEP-013": "international",
    "TEP-014": "conference",
    "COC-001": "noise_conduct",
    "HRP-015": "noise_remote_stipend",
    "HR-104": "noise_inclusion",
    "HR-208": "noise_referral",
    "HR-302": "noise_charitable",
    "REC-001": "noise_records",
    "LEG-101": "noise_whistleblower",
    "LEG-203": "noise_ip",
    "PRIV-101": "noise_privacy",
    "SEC-201": "noise_data_class",
    "SEC-202": "noise_aup",
    "SEC-204": "noise_mobile",
    "SEC-301": "international",
    "FAC-005": "noise_office_safety",
    "PROC-002": "noise_vendor",
    "SUS-001": "noise_sustainability",
    "BC-001": "noise_bcp",
}


HEADER_RE = re.compile(
    r"Document:\s*([A-Z]{2,4}-\d{3})\s*Version:\s*([\d.]+)", re.IGNORECASE
)
TITLE_RE = re.compile(r"^([A-Z][A-Za-z &\-/]+?)$")
# A section heading like "2.3." or "2.3" followed by optional title text
SECTION_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+(.*)$")


# ---- Clause typing -------------------------------------------------------
# Order matters: the first matching rule wins, so the most consequential
# label sits at the top. A clause that contains both "must" and "unless"
# is a hard rule with a carve-out, but the safety-relevant tag is hard_rule.
_HARD_RULE_RE = re.compile(
    r"\b(must|shall|will not|may not|are prohibited|is prohibited|"
    r"not reimbursable|never reimbursable|are never|is required|requires?\b)",
    re.IGNORECASE,
)
_EXCEPTION_RE = re.compile(
    r"\b(exception|exempt(?:ion)?|unless|provided that|except (?:by|where|when)|"
    r"requires?\s+(?:prior\s+)?(?:written\s+)?approval)",
    re.IGNORECASE,
)
_GUIDANCE_RE = re.compile(
    r"\b(should|preferred|encouraged|consider|may\b|whenever practical)",
    re.IGNORECASE,
)
_DEFINITION_HEADERS = {"definitions", "glossary"}
_INFORMATIONAL_HEADERS = {
    "purpose", "scope", "related policies", "related documents",
    "document control", "audit and enforcement",
}

# Domain keywords we always want surfaced if present; helps keyword fallback
# and gives the UI a chance to render badges on cards.
_DOMAIN_TRIGGERS = {
    "alcohol", "wine", "beer", "lodging", "hotel", "per-diem", "per diem",
    "meal", "breakfast", "lunch", "dinner", "cap", "tier", "economy",
    "premium", "business class", "first class", "concur", "receipt",
    "itemized", "tip", "gratuity", "international", "vp approval",
    "director approval", "manager approval", "reimbursable", "non-reimbursable",
    "prohibited", "approval", "exception", "grade",
}

_STOPWORDS = {
    "the", "and", "for", "are", "with", "that", "this", "from", "will",
    "shall", "have", "has", "any", "all", "may", "must", "per", "not",
    "under", "over", "upon", "into", "such", "each", "other", "who",
    "which", "where", "when", "then", "than", "these", "those", "their",
    "there", "about", "into", "also", "can", "company", "employee",
    "employees", "policy", "policies", "document", "section", "see",
}
_WORD_RE = re.compile(r"[a-z][a-z\-]{3,}")


def _classify_clause(section: str, title: str, text: str) -> str:
    """Return one of hard_rule / guidance / exception / definition / informational."""
    title_l = (title or "").strip().lower()
    if title_l in _DEFINITION_HEADERS or section.startswith(("8.", "9.", "10.", "11.", "12.", "13.")) and "definition" in title_l:
        return "definition"
    if title_l in _INFORMATIONAL_HEADERS:
        return "informational"
    if title_l == "exceptions" or (section.startswith("7") and "exception" in title_l):
        return "exception"

    if _HARD_RULE_RE.search(text):
        # "requires VP approval" type clauses are hard rules even though they
        # also read as exceptions; the consequence is mandatory.
        return "hard_rule"
    if _EXCEPTION_RE.search(text):
        return "exception"
    if _GUIDANCE_RE.search(text):
        return "guidance"
    return "informational"


def _extract_keywords(text: str, limit: int = 10) -> list[str]:
    """Cheap keyword extraction: domain triggers + top non-stopword tokens."""
    text_l = text.lower()
    hits: list[str] = []
    for kw in _DOMAIN_TRIGGERS:
        if kw in text_l and kw not in hits:
            hits.append(kw)
    # token frequency for everything else
    freq: dict[str, int] = {}
    for tok in _WORD_RE.findall(text_l):
        if tok in _STOPWORDS or tok in hits:
            continue
        freq[tok] = freq.get(tok, 0) + 1
    extra = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    for tok, _ in extra:
        if len(hits) >= limit:
            break
        hits.append(tok)
    return hits[:limit]


def _embedding_text(doc_id: str, section: str, clause_title: str | None, text: str) -> str:
    """Prepend doc + section + title so the embedding carries scope context.
    The body is still the only thing a quote is built from; this is purely a
    retrieval-quality lever, not a citation surface."""
    header = f"[{doc_id} \u00a7{section}]"
    if clause_title:
        header += f" {clause_title}"
    return f"{header}\n{text}"


def _read_pdf_text(path: Path) -> str:
    reader = pypdf.PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _split_documents(full_text: str) -> list[str]:
    """A single PDF can hold multiple policy documents. We split on the
    'Document: XXX-NNN Version: ...' header line."""
    parts: list[str] = []
    matches = list(HEADER_RE.finditer(full_text))
    if not matches:
        return [full_text]
    for i, m in enumerate(matches):
        start = m.start()
        # back up to nearest preceding line break so we capture the title line
        line_start = full_text.rfind("\n", 0, start)
        line_start = 0 if line_start == -1 else line_start + 1
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        # again back up so end-marker line stays with next doc
        end_line_start = full_text.rfind("\n", 0, end) if i + 1 < len(matches) else end
        end_line_start = end if end_line_start == -1 else end_line_start
        parts.append(full_text[line_start:end_line_start])
    return parts


def _extract_clauses(
    doc_text: str,
) -> tuple[str, str, str, list[tuple[str, str, str, str]]]:
    """Return (doc_id, version, doc_title, [(section, clause_title, text, quote)]).

    `text` is whitespace-normalised for embedding/search. `quote` is the
    verbatim text preserving the original line breaks — this is what the UI
    surfaces as the citation. Keeping both eliminates the choice between
    'good for search' vs 'good for reviewers' and means we never paraphrase
    a clause we cite.
    """
    m = HEADER_RE.search(doc_text)
    doc_id = m.group(1).upper() if m else "UNKNOWN"
    version = m.group(2) if m else ""
    # doc title is the line right before the Document header
    header_line_start = doc_text.rfind("\n", 0, m.start()) if m else -1
    title = ""
    if header_line_start != -1:
        title = doc_text[:header_line_start].strip().splitlines()[-1].strip()

    lines = doc_text.splitlines()
    clauses: list[tuple[str, str, str, str]] = []
    cur_section: str | None = None
    cur_title: str = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf, cur_section, cur_title
        if cur_section and buf:
            quote = "\n".join(buf).rstrip()
            text = re.sub(r"\s+", " ", " ".join(s.strip() for s in buf if s.strip())).strip()
            if text:
                clauses.append((cur_section, cur_title, text, quote))
        buf = []

    for line in lines:
        sm = SECTION_RE.match(line)
        if sm:
            flush()
            cur_section = sm.group(1)
            cur_title = sm.group(2).strip()
            # keep the title line as part of the clause body too
            if cur_title:
                buf.append(cur_title)
        else:
            buf.append(line)
    flush()

    return doc_id, version, title, clauses


def ingest_policies(policy_dir: str | None = None) -> int:
    """Parse all policies in policy_dir and write rows to policy_clauses.

    Idempotent: clears table first.
    """
    s = get_settings()
    pdir = Path(policy_dir or s.policy_dir)
    pdfs = sorted(pdir.glob("*.pdf"))
    if not pdfs:
        log.warning("No PDFs in %s", pdir)
        return 0

    rows: list[PolicyClause] = []
    embed_inputs: list[str] = []
    for pdf in pdfs:
        text = _read_pdf_text(pdf)
        for doc_chunk in _split_documents(text):
            doc_id, version, title, clauses = _extract_clauses(doc_chunk)
            family = FAMILY_BY_DOC.get(doc_id, "unknown")
            for section, ctitle, body, quote in clauses:
                ctype = _classify_clause(section, ctitle, body)
                keywords = _extract_keywords(body)
                rows.append(
                    PolicyClause(
                        doc_id=doc_id,
                        doc_title=title or doc_id,
                        doc_version=version,
                        section=section,
                        clause_title=ctitle or None,
                        text=body,
                        quote=quote,
                        policy_family=family,
                        clause_type=ctype,
                        keywords=keywords,
                    )
                )
                embed_inputs.append(_embedding_text(doc_id, section, ctitle, body))

    # Embed (best-effort) — prepended doc+section header improves retrieval
    # without polluting the citation surface (which uses `quote`).
    embeddings = embed(embed_inputs) if embed_inputs else None
    if embeddings is not None:
        for r, e in zip(rows, embeddings):
            r.embedding = e

    with session_scope() as db:
        db.query(PolicyClause).delete()
        db.add_all(rows)
    log.info("Ingested %d clauses from %d PDFs", len(rows), len(pdfs))
    return len(rows)


def find_clauses(
    db,
    query: str,
    families: Iterable[str] | None = None,
    k: int = 6,
    keywords: Iterable[str] | None = None,
    prefer_types: Iterable[str] | None = ("hard_rule", "exception", "guidance"),
) -> list[PolicyClause]:
    """Hybrid retrieval: family filter + vector similarity + keyword overlap
    + clause-type preference.

    The candidate pool is `4*k` (semantic) merged with up to `2*k` keyword
    hits, then re-ranked by:
        score = semantic_rank_weight + keyword_overlap_weight + type_boost
    Returns the top `k` `PolicyClause` rows (no scores attached \u2014 caller
    treats results as ordered-by-relevance).
    """
    base = db.query(PolicyClause)
    if families:
        fam_list = list(families)
        if fam_list:
            base = base.filter(PolicyClause.policy_family.in_(fam_list))

    candidates: dict[int, tuple[PolicyClause, float]] = {}

    # --- 1. Semantic candidates ---------------------------------------
    emb = embed([query])
    if emb:
        target = emb[0]
        semantic = (
            base.order_by(PolicyClause.embedding.cosine_distance(target))
            .limit(k * 4)
            .all()
        )
        # rank-based score: 1.0 for #1, decays
        n = max(len(semantic), 1)
        for i, row in enumerate(semantic):
            candidates[row.id] = (row, 1.0 - i / n)

    # --- 2. Keyword candidates (always; even when semantic is on) -----
    kw_terms: list[str] = []
    if keywords:
        kw_terms.extend(k_.lower() for k_ in keywords if k_)
    # also pull 4-char+ tokens from the free-text query
    kw_terms.extend(t.lower() for t in re.findall(r"\w{4,}", query)[:8])
    kw_terms = list(dict.fromkeys(kw_terms))[:10]

    if kw_terms:
        from sqlalchemy import or_

        conds = [PolicyClause.text.ilike(f"%{t}%") for t in kw_terms]
        kw_hits = base.filter(or_(*conds)).limit(k * 2).all()
        for row in kw_hits:
            # +0.3 per matching keyword (capped) so multi-hit clauses bubble up
            text_low = (row.text or "").lower()
            overlap = sum(1 for t in kw_terms if t in text_low)
            kw_score = min(0.3 * overlap, 0.9)
            prev = candidates.get(row.id)
            if prev:
                candidates[row.id] = (row, prev[1] + kw_score)
            else:
                candidates[row.id] = (row, kw_score)

    if not candidates:
        return list(base.limit(k))

    # --- 3. Clause-type boost -----------------------------------------
    type_boost = {t: 0.25 for t in (prefer_types or ())}
    type_boost.setdefault("informational", -0.1)

    scored = []
    for row, score in candidates.values():
        boost = type_boost.get(row.clause_type or "informational", 0.0)
        scored.append((row, score + boost))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [row for row, _ in scored[:k]]

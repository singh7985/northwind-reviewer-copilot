"""Receipt extraction: PDF / image / text → ExtractedReceipt.

Strategy:
1. Always extract a text trace (pypdf for PDFs, base64 image for vision input,
   plain text for .txt) so we have a permanent raw_text trail.
2. If LLM is enabled, call gpt-4o-mini with the file/image + a strict
   JSON-schema response_format to populate ExtractedReceipt.
3. If LLM is disabled or the call fails, fall back to a regex/heuristic
   parser that pulls merchant, date, total, tip, alcohol indicators from the
   raw text. This keeps the eval harness fully runnable offline.
"""
from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Optional

import pypdf
from PIL import Image

from ..config import get_settings
from ..schemas import ExtractedReceipt, LineItem
from .llm import chat_structured


log = logging.getLogger(__name__)


EXTRACTION_SYSTEM = """You extract structured data from a single business receipt.
You will be given the receipt as text and/or an image.

Rules:
- Return only the JSON object that matches the provided schema.
- Use null/empty when the receipt does not show a field — never guess.
- Set extraction_confidence between 0 and 1 based on how legible and complete the receipt is.
- For meals, set category to meal_breakfast/lunch/dinner based on time-of-day or item context.
  Default unclear meals to meal_other.
- For alcohol: alcohol_present=true if any line item is beer/wine/spirits/cocktail/seltzer.
  Sum the alcohol charges into alcohol_amount.
- For rideshare: rideshare_tier is "premium" for Uber Black/Lyft Lux/Premier/SUV/XL Plus,
  "standard" for UberX/Lyft Standard.
- For flights: flight_class is one of economy/premium_economy/business/first.
- For lodging: nightly_rate = total / hotel_nights when both visible; otherwise leave null.
- claimed_amount = total (what the employee is asking the company to reimburse).
"""


def _read_pdf_text(path: Path) -> str:
    try:
        r = pypdf.PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception as e:
        log.warning("PDF read failed for %s: %s", path, e)
        return ""


def _load_image_b64(path: Path) -> Optional[str]:
    try:
        img = Image.open(path)
        # ensure RGB jpeg
        buf_path = path
        if img.mode not in ("RGB", "L"):
            from io import BytesIO
            bio = BytesIO()
            img.convert("RGB").save(bio, format="JPEG")
            return base64.b64encode(bio.getvalue()).decode()
        return base64.b64encode(buf_path.read_bytes()).decode()
    except Exception as e:
        log.warning("Image load failed for %s: %s", path, e)
        return None


def extract_receipt(file_path: str, mime_type: str) -> ExtractedReceipt:
    s = get_settings()
    path = Path(file_path)
    raw_text = ""
    user_parts: list[dict] = []

    if mime_type == "application/pdf" or path.suffix.lower() == ".pdf":
        raw_text = _read_pdf_text(path)
        user_parts.append({"type": "text", "text": f"Receipt text:\n{raw_text}"})
    elif mime_type.startswith("image/") or path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        b64 = _load_image_b64(path)
        if b64:
            ext = "jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "png"
            user_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/{ext};base64,{b64}"},
                }
            )
        user_parts.append({"type": "text", "text": "Image attached."})
    else:
        try:
            raw_text = path.read_text(errors="ignore")
        except Exception:
            raw_text = ""
        user_parts.append({"type": "text", "text": f"Receipt text:\n{raw_text}"})

    extracted: Optional[ExtractedReceipt] = None
    image_attempted = any(p.get("type") == "image_url" for p in user_parts)

    if s.llm_enabled:
        extracted = chat_structured(
            model=s.openai_extraction_model,
            schema_cls=ExtractedReceipt,
            system=EXTRACTION_SYSTEM,
            user_parts=user_parts,
            temperature=0.0,
        )

    if extracted is None:
        extracted = _heuristic_extract(raw_text, path)

    # ---- Audit + fallback handling -------------------------------------
    # Always carry the full raw text alongside structured fields so that:
    #  - reviewers can spot-check what the model "saw",
    #  - the heuristic fallback has a permanent trace, and
    #  - duplicate-detection / re-processing never re-reads the file.
    extracted.raw_text = raw_text or None
    if extracted.notes is None and raw_text:
        # Keep `notes` as a short LLM-facing summary; cap to avoid bloating
        # the JSON column in DB.
        extracted.notes = raw_text[:4000]

    issues: list[str] = list(extracted.extraction_issues or [])

    # Unreadable: no text trace AND no successful image submission.
    if not raw_text and not image_attempted:
        issues.append("unreadable")
        extracted.extraction_confidence = min(extracted.extraction_confidence, 0.1)

    # Missing itemization: a total is asserted but neither subtotal nor any
    # line items support it. This is the classic "summary-only" receipt that
    # should escalate to human review even if the dollar amount is parseable.
    if extracted.total and not extracted.subtotal and not extracted.line_items:
        issues.append("missing_itemization")

    # Partial: category-specific fields missing where they should be present.
    if extracted.category and extracted.category != "unknown":
        partial = False
        cat = extracted.category
        if cat == "lodging" and not (extracted.hotel_nights or extracted.nightly_rate):
            partial = True
        elif cat == "air_travel" and not extracted.flight_class:
            partial = True
        elif cat.startswith("meal_") and not extracted.total:
            partial = True
        if partial:
            issues.append("partial")

    # Low-quality floor: the gate that triggers needs_human_review downstream.
    if extracted.extraction_confidence < 0.35:
        issues.append("low_quality")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    extracted.extraction_issues = [c for c in issues if not (c in seen or seen.add(c))]
    return extracted


# ---------------- Heuristic fallback (offline, deterministic) ----------------

MONEY_RE = re.compile(r"\$?\s*([0-9]+(?:[.,][0-9]{2}))")
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/20\d{2})")
ALCOHOL_TERMS = re.compile(
    r"\b(beer|wine|ipa|lager|cabernet|merlot|pinot|chardonnay|whisk|whiskey|vodka|gin|"
    r"tequila|cocktail|margarita|martini|champagne|prosecco|rosé|rose|seltzer|spirits|"
    r"bourbon|scotch|sake)\b",
    re.IGNORECASE,
)
PREMIUM_RIDESHARE_RE = re.compile(
    r"\b(uber\s*black|lyft\s*lux|premier|black\s*suv|lux\s*black)\b", re.IGNORECASE
)


def _heuristic_extract(text: str, path: Path) -> ExtractedReceipt:
    e = ExtractedReceipt()
    if not text:
        e.extraction_confidence = 0.1
        e.notes = f"No text extracted from {path.name}"
        return e

    low = text.lower()
    name = path.name.lower()

    # merchant heuristic: first non-empty line
    for line in text.splitlines():
        line = line.strip()
        if line and not line[0].isdigit():
            e.merchant = line[:120]
            break

    # date
    dm = DATE_RE.search(text)
    if dm:
        e.transaction_date = dm.group(1)

    # totals: take the largest money figure as a guess of total
    amounts = [float(x.replace(",", "")) for x in MONEY_RE.findall(text)]
    if amounts:
        e.total = max(amounts)
        e.claimed_amount = e.total

    # tip
    tm = re.search(r"tip[^0-9]{0,10}\$?\s*([0-9]+(?:[.,][0-9]{2}))", text, re.IGNORECASE)
    if tm:
        e.tip = float(tm.group(1).replace(",", ""))
    sm = re.search(r"sub[\s\-]*total[^0-9]{0,10}\$?\s*([0-9]+(?:[.,][0-9]{2}))", text, re.IGNORECASE)
    if sm:
        e.subtotal = float(sm.group(1).replace(",", ""))

    # alcohol
    if ALCOHOL_TERMS.search(text):
        e.alcohol_present = True
        # naive: sum amounts on lines containing alcohol terms
        alc = 0.0
        for line in text.splitlines():
            if ALCOHOL_TERMS.search(line):
                mm = MONEY_RE.findall(line)
                if mm:
                    alc += float(mm[-1].replace(",", ""))
        if alc:
            e.alcohol_amount = round(alc, 2)
    else:
        e.alcohol_present = False

    # category from filename hints
    if any(k in name for k in ["flight", "airlines", "delta", "united", "american", "southwest", "alaska"]):
        e.category = "air_travel"
        if "first" in low:
            e.flight_class = "first"
        elif "business" in low:
            e.flight_class = "business"
        elif "premium" in low:
            e.flight_class = "premium_economy"
        else:
            e.flight_class = "economy"
    elif any(k in name for k in ["hotel", "marriott", "hilton", "hyatt", "lodging", "inn"]):
        e.category = "lodging"
        nm = re.search(r"(\d+)\s*nights?", low)
        if nm:
            e.hotel_nights = int(nm.group(1))
        rm = re.search(r"(?:nightly|per\s+night|room\s+rate)[^0-9]{0,12}\$?\s*([0-9]+(?:[.,][0-9]{2}))", low)
        if rm:
            e.nightly_rate = float(rm.group(1).replace(",", ""))
        elif e.total and e.hotel_nights:
            e.nightly_rate = round(e.total / e.hotel_nights, 2)
        if "mini" in low and "bar" in low:
            e.has_minibar_charge = True
    elif "uber" in name or "lyft" in name or "taxi" in name:
        e.category = "ground_rideshare" if ("uber" in name or "lyft" in name) else "ground_taxi"
        e.rideshare_tier = "premium" if PREMIUM_RIDESHARE_RE.search(text) else "standard"
    elif "conference" in name or "registration" in name:
        e.category = "conference_registration"
    elif "breakfast" in name:
        e.category = "meal_breakfast"
    elif "lunch" in name:
        e.category = "meal_lunch"
    elif "dinner" in name:
        e.category = "meal_dinner"

    e.extraction_confidence = 0.55 if e.total else 0.3
    return e

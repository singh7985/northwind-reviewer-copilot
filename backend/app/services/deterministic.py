"""Deterministic compliance engine.

Phase 5 layout:
    1. `classify_category(extracted)` — keyword/merchant-based classifier that
       confirms or repairs the LLM's `extracted.category`. Used as a defensive
       layer when the LLM is offline or guessed wrong.
    2. `RuleResult` — the uniform return type for every rule:
           - status: pass / fail / unknown
           - explanation: human-readable, cite-ready sentence
           - facts: structured supporting data (numbers, names, derived flags)
           - policy_refs: stable doc IDs for the retriever to fetch
           - severity + amount_affected: how the finding should be enforced
    3. `check_*` — one function per policy domain. Each is pure (extracted +
       context in, RuleResult(s) out) so it can be unit-tested in isolation
       and reused outside the orchestrator.
    4. `run_rules(...)` — orchestrator that calls every per-receipt rule,
       drops `pass`/`unknown` results, and converts `fail` results to the
       legacy `DeterministicFinding` shape the adjudicator already consumes.
    5. `check_submission_approval_threshold(...)` — submission-scoped rule
       (operates on the full receipt list).
    6. `deterministic_verdict(...)` — preliminary verdict + reimbursement
       arithmetic. The LLM treats `reject` findings as non-negotiable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional

from ..schemas import DeterministicFinding, ExtractedReceipt


# ============================================================
# Reference data (caps, tiers)
# ============================================================

# Internal city tiers used for lodging/meal benchmarks. These tier lists
# are an internal Finance-Ops convention; they are NOT defined in the
# policy corpus and are enforced under TEP-001 §3.2 (Reasonableness).
TIER1 = {
    "new york", "san francisco", "boston", "washington", "los angeles", "seattle",
    "london", "zurich", "tokyo", "singapore",
}
TIER2 = {
    "chicago", "denver", "atlanta", "austin", "dallas", "houston", "miami",
    "portland", "san diego", "toronto", "amsterdam", "berlin", "sydney",
}
# Internal lodging benchmarks (not in corpus; anchored on TEP-001 §3.2).
LODGING_CAPS = {"tier1": 350, "tier2": 250, "tier3": 175}

# Internal meal benchmarks (not in corpus; anchored on TEP-001 §3.2).
MEAL_CAPS = {"meal_breakfast": 25, "meal_lunch": 35, "meal_dinner": 75}
MEAL_CATEGORIES = set(MEAL_CAPS.keys()) | {"meal_other"}
TIER1_UPLIFT = 1.25
TIP_CAP_PCT = 0.20
ALCOHOL_PER_PERSON_CAP = 50.0
SUBMISSION_VP_THRESHOLD = 5_000.0  # TEP-001 §4.3: VP sign-off above $5k


# ============================================================
# Result type
# ============================================================

Status = Literal["pass", "fail", "unknown"]
Severity = Literal["info", "flag", "reject"]


@dataclass
class RuleResult:
    """Uniform output of every `check_*` function.

    `status` is the rule's own judgement; `severity` only matters when
    `status == "fail"` (it tells the verdict layer how to enforce).
    """

    rule_id: str
    status: Status
    explanation: str
    facts: dict[str, Any] = field(default_factory=dict)
    policy_refs: list[str] = field(default_factory=list)
    severity: Severity = "info"
    amount_affected: Optional[float] = None

    def to_finding(self) -> Optional[DeterministicFinding]:
        """Convert a `fail` result to the legacy finding shape. Returns None
        for `pass` and `unknown`."""
        if self.status != "fail":
            return None
        return DeterministicFinding(
            rule_id=self.rule_id,
            severity=self.severity,
            message=self.explanation,
            policy_refs=list(self.policy_refs),
            amount_affected=self.amount_affected,
        )


# ============================================================
# 1. Category classifier
# ============================================================

# Stable category vocabulary used across the system.
CATEGORIES = (
    "air_travel",
    "lodging",
    "ground_rideshare",
    "ground_taxi",
    "ground_parking",
    "ground_transit",
    "meal_breakfast",
    "meal_lunch",
    "meal_dinner",
    "meal_other",
    "conference_registration",
    "other",
    "unknown",
)

_AIRLINE_TOKENS = (
    "delta", "united", "american airlines", "southwest", "alaska airlines",
    "jetblue", "lufthansa", "british airways", "klm", "air france",
    "emirates", "qantas", "ana", "cathay",
)
_HOTEL_TOKENS = (
    "hotel", "marriott", "hilton", "hyatt", "sheraton", "westin", "inn",
    "lodging", "resort", "ritz", "four seasons", "holiday inn", "courtyard",
    "embassy suites", "doubletree",
)
_RIDESHARE_TOKENS = ("uber", "lyft")
_TAXI_TOKENS = ("taxi", "cab", "yellow cab", "checker")
_PARKING_TOKENS = ("parking", "garage", "valet", "lot")
_TRANSIT_TOKENS = (
    "metro", "subway", "amtrak", "transit", "rail", "mta", "bart", "caltrain",
    "metro-north", "lirr", "septa", "wmata", "tfl",
)
_CONFERENCE_TOKENS = (
    "conference", "summit", "registration", "expo", "convention", "symposium",
    "kubecon", "re:invent", "dreamforce", "ignite",
)


def classify_category(extracted: ExtractedReceipt, filename: str = "") -> str:
    """Deterministic category classifier.

    Prefers an LLM-supplied `extracted.category` when it's a known value,
    otherwise infers from merchant text and filename hints. Always returns
    a member of `CATEGORIES`.
    """
    cat = (extracted.category or "").strip().lower()
    if cat in CATEGORIES and cat != "unknown":
        return cat

    haystacks = " ".join(
        [
            (extracted.merchant or ""),
            filename or "",
            (extracted.notes or "")[:500],
        ]
    ).lower()

    def _hit(tokens: Iterable[str]) -> bool:
        return any(t in haystacks for t in tokens)

    if _hit(_AIRLINE_TOKENS) or "flight" in haystacks or "airfare" in haystacks:
        return "air_travel"
    if _hit(_HOTEL_TOKENS):
        return "lodging"
    if _hit(_RIDESHARE_TOKENS):
        return "ground_rideshare"
    if _hit(_TAXI_TOKENS):
        return "ground_taxi"
    if _hit(_PARKING_TOKENS):
        return "ground_parking"
    if _hit(_TRANSIT_TOKENS):
        return "ground_transit"
    if _hit(_CONFERENCE_TOKENS):
        return "conference_registration"

    # meals fallback: time-of-day keywords in filename
    if "breakfast" in haystacks:
        return "meal_breakfast"
    if "lunch" in haystacks:
        return "meal_lunch"
    if "dinner" in haystacks:
        return "meal_dinner"
    return "unknown"


# ============================================================
# 2. Helpers
# ============================================================

def city_tier(city: Optional[str]) -> str:
    if not city:
        return "tier3"
    c = city.lower()
    for t1 in TIER1:
        if t1 in c:
            return "tier1"
    for t2 in TIER2:
        if t2 in c:
            return "tier2"
    return "tier3"


def _trip_is_solo(trip_purpose: str) -> bool:
    p = (trip_purpose or "").lower()
    return "solo" in p or "alone" in p


def _has_external_attendees(extracted: ExtractedReceipt, trip_purpose: str) -> bool:
    p = (trip_purpose or "").lower()
    if any(k in p for k in ["client", "customer", "vendor", "prospect", "partner", "qbr"]):
        return bool(extracted.attendees) or False
    return bool(extracted.attendees)


def _attendee_count(extracted: ExtractedReceipt) -> int:
    if extracted.attendees:
        return max(len(extracted.attendees), 1)
    return 1


# ============================================================
# 3. Per-receipt rule functions
# ============================================================

def check_meal_cap(
    extracted: ExtractedReceipt,
    *,
    trip_purpose: str = "",
    trip_nights: Optional[int] = None,
    employee_grade: int = 1,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> RuleResult:
    """Per-meal internal benchmark with TIER1 1.25x uplift.

    The corpus does not contain a dedicated meals policy, so the dollar
    amounts are an internal Finance-Ops benchmark enforced under
    TEP-001 §3.2 (Reasonableness)."""
    cat = extracted.category or "unknown"
    if cat not in MEAL_CAPS:
        return RuleResult(
            "meal_cap_exceeded", "unknown",
            "Not a meal category — rule not applicable.",
            facts={"category": cat},
            policy_refs=["TEP-001 §3.2"],
        )
    total = extracted.total or 0.0
    if not total:
        return RuleResult(
            "meal_cap_exceeded", "unknown",
            "No total on receipt; cannot evaluate meal benchmark.",
            facts={"category": cat},
            policy_refs=["TEP-001 §3.2"],
        )
    cap = MEAL_CAPS[cat]
    tier = city_tier(extracted.city)
    if tier == "tier1":
        cap = round(cap * TIER1_UPLIFT, 2)
    # exclude alcohol from the food-cap basis (alcohol has its own rule)
    food = round(total - (extracted.alcohol_amount or 0.0), 2)
    facts = {
        "category": cat,
        "food_total": food,
        "cap": cap,
        "city_tier": tier,
        "alcohol_excluded": extracted.alcohol_amount or 0.0,
    }
    if food > cap:
        return RuleResult(
            "meal_cap_exceeded", "fail",
            f"{cat.replace('meal_', '').title()} of ${food:.2f} exceeds the "
            f"${cap:.2f} {tier.upper()} internal benchmark; enforced under "
            "TEP-001 §3.2 (Reasonableness).",
            facts=facts,
            policy_refs=["TEP-001 §3.2"],
            severity="flag",
            amount_affected=round(food - cap, 2),
        )
    return RuleResult(
        "meal_cap_exceeded", "pass",
        f"${food:.2f} is within the ${cap:.2f} {tier.upper()} benchmark.",
        facts=facts,
        policy_refs=["TEP-001 §3.2"],
    )


def check_tip_cap(
    extracted: ExtractedReceipt,
    *,
    trip_purpose: str = "",
    trip_nights: Optional[int] = None,
    employee_grade: int = 1,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> RuleResult:
    """Tip reimbursable up to 20% of pre-tax subtotal.

    The corpus has no tip-specific clause; the 20% threshold is an internal
    Finance-Ops benchmark enforced under TEP-001 §3.2 (Reasonableness)."""
    cat = extracted.category or "unknown"
    if cat not in MEAL_CATEGORIES:
        return RuleResult(
            "tip_over_20pct", "unknown",
            "Tip benchmark only applies to meals.",
            facts={"category": cat},
            policy_refs=["TEP-001 §3.2"],
        )
    if extracted.subtotal is None or extracted.tip is None:
        return RuleResult(
            "tip_over_20pct", "unknown",
            "Subtotal or tip missing; cannot evaluate tip benchmark.",
            facts={"subtotal": extracted.subtotal, "tip": extracted.tip},
            policy_refs=["TEP-001 §3.2"],
        )
    if extracted.subtotal <= 0:
        return RuleResult(
            "tip_over_20pct", "unknown",
            "Subtotal is zero or negative.",
            facts={"subtotal": extracted.subtotal},
            policy_refs=["TEP-001 §3.2"],
        )
    pct = extracted.tip / extracted.subtotal
    facts = {
        "subtotal": extracted.subtotal,
        "tip": extracted.tip,
        "tip_pct": round(pct, 4),
        "cap_pct": TIP_CAP_PCT,
    }
    if pct > TIP_CAP_PCT + 1e-6:
        excess = round(extracted.tip - extracted.subtotal * TIP_CAP_PCT, 2)
        return RuleResult(
            "tip_over_20pct", "fail",
            f"Tip ${extracted.tip:.2f} is {pct * 100:.1f}% of pre-tax "
            f"${extracted.subtotal:.2f}; only the 20% internal benchmark is "
            "reimbursable under TEP-001 §3.2 (Reasonableness).",
            facts=facts,
            policy_refs=["TEP-001 §3.2"],
            severity="flag",
            amount_affected=excess,
        )
    return RuleResult(
        "tip_over_20pct", "pass",
        f"Tip {pct * 100:.1f}% is within the 20% internal benchmark.",
        facts=facts,
        policy_refs=["TEP-001 §3.2"],
    )


def check_alcohol_policy(
    extracted: ExtractedReceipt,
    *,
    trip_purpose: str = "",
    trip_nights: Optional[int] = None,
    employee_grade: int = 1,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> list[RuleResult]:
    """Alcohol rules.

    The corpus does not contain a dedicated alcohol policy. Two principled
    anchors are used:
      * Solo travel / no external attendees → TEP-001 §3.1 (Business Purpose):
        alcohol without a documented client-facing business purpose is not
        reimbursable.
      * Per-person internal cap + VP-approval flag → TEP-001 §3.2
        (Reasonableness) as an internal Finance-Ops benchmark.
    """
    if not extracted.alcohol_present or not (extracted.alcohol_amount or 0) > 0:
        return [
            RuleResult(
                "alcohol_policy", "pass",
                "No alcohol charges on this receipt.",
                facts={"alcohol_present": bool(extracted.alcohol_present)},
                policy_refs=["TEP-001 §3.1", "TEP-001 §3.2"],
            )
        ]

    amt = float(extracted.alcohol_amount or 0.0)
    solo = _trip_is_solo(trip_purpose)
    external = _has_external_attendees(extracted, trip_purpose)
    n_attendees = _attendee_count(extracted)
    per_person = round(amt / n_attendees, 2)
    base_facts = {
        "alcohol_amount": amt,
        "solo_trip": solo,
        "external_attendees": external,
        "attendee_count": n_attendees,
        "per_person": per_person,
        "per_person_cap": ALCOHOL_PER_PERSON_CAP,
    }
    results: list[RuleResult] = []

    if solo:
        results.append(RuleResult(
            "alcohol_solo_travel", "fail",
            f"Alcohol charge ${amt:.2f} on solo travel lacks a documented "
            "business purpose (TEP-001 §3.1).",
            facts=base_facts,
            policy_refs=["TEP-001 §3.1"],
            severity="reject",
            amount_affected=amt,
        ))
        return results

    if not external:
        results.append(RuleResult(
            "alcohol_no_external_attendee", "fail",
            f"Alcohol charge ${amt:.2f} requires a documented client-facing "
            "business purpose with at least one external attendee "
            "(TEP-001 §3.1).",
            facts=base_facts,
            policy_refs=["TEP-001 §3.1"],
            severity="reject",
            amount_affected=amt,
        ))
        return results

    if per_person > ALCOHOL_PER_PERSON_CAP:
        excess = round((per_person - ALCOHOL_PER_PERSON_CAP) * n_attendees, 2)
        results.append(RuleResult(
            "alcohol_per_person_cap", "fail",
            f"Alcohol ${per_person:.2f}/person exceeds the "
            f"${ALCOHOL_PER_PERSON_CAP:.0f}/person internal benchmark; "
            "enforced under TEP-001 §3.2 (Reasonableness).",
            facts=base_facts,
            policy_refs=["TEP-001 §3.2"],
            severity="flag",
            amount_affected=excess,
        ))

    # Internal control: surface alcohol so an approver reviews it as part
    # of client entertainment. Anchored on Reasonableness.
    results.append(RuleResult(
        "alcohol_review_required", "fail",
        "Alcohol present with client-entertainment context — surface for "
        "approver review under TEP-001 §3.2 (Reasonableness).",
        facts=base_facts,
        policy_refs=["TEP-001 §3.2"],
        severity="flag",
    ))
    return results


def check_lodging_cap(
    extracted: ExtractedReceipt,
    *,
    trip_purpose: str = "",
    trip_nights: Optional[int] = None,
    employee_grade: int = 1,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> list[RuleResult]:
    """Lodging nightly internal benchmark + mini-bar + room-upgrade.

    The corpus does not contain a dedicated lodging policy. Nightly
    benchmarks are internal Finance-Ops values enforced under TEP-001 §3.2
    (Reasonableness). International lodging also references TEP-013 §4.1.
    """
    cat = extracted.category or "unknown"
    if cat != "lodging":
        return [RuleResult(
            "lodging_over_cap", "unknown",
            "Not a lodging receipt.",
            facts={"category": cat},
            policy_refs=["TEP-001 §3.2"],
        )]

    results: list[RuleResult] = []
    nightly = extracted.nightly_rate
    nights = extracted.hotel_nights
    total = extracted.total or 0.0
    if not nightly and nights and total:
        nightly = round(total / nights, 2)
    tier = city_tier(extracted.city)
    cap = LODGING_CAPS.get(tier, 175)
    is_intl = bool(extracted.is_international)
    refs = ["TEP-001 §3.2"] + (["TEP-013 §4.1"] if is_intl else [])
    facts = {
        "city": extracted.city,
        "city_tier": tier,
        "nightly_rate": nightly,
        "nights": nights,
        "cap": cap,
        "international": is_intl,
    }
    if nightly:
        if nightly > cap:
            results.append(RuleResult(
                "lodging_over_cap", "fail",
                f"Nightly rate ${nightly:.2f} exceeds the {tier.upper()} "
                f"${cap} internal benchmark; enforced under "
                "TEP-001 §3.2 (Reasonableness).",
                facts=facts,
                policy_refs=refs,
                severity="flag",
                amount_affected=round((nightly - cap) * (nights or 1), 2),
            ))
        else:
            results.append(RuleResult(
                "lodging_over_cap", "pass",
                f"Nightly rate ${nightly:.2f} within {tier.upper()} "
                f"${cap} benchmark.",
                facts=facts,
                policy_refs=refs,
            ))
    else:
        results.append(RuleResult(
            "lodging_over_cap", "unknown",
            "No nightly rate and no nights/total combo to derive it.",
            facts=facts,
            policy_refs=refs,
        ))

    if extracted.has_minibar_charge:
        results.append(RuleResult(
            "minibar_non_reimbursable", "fail",
            "Mini-bar charges lack a documented business purpose "
            "(TEP-001 §3.1).",
            facts={"minibar": True},
            policy_refs=["TEP-001 §3.1"],
            severity="reject",
        ))
    if extracted.has_room_upgrade:
        results.append(RuleResult(
            "room_upgrade", "fail",
            "Voluntary room upgrade fails the lowest-practical-cost test "
            "(TEP-001 §3.2).",
            facts={"room_upgrade": True},
            policy_refs=["TEP-001 §3.2"],
            severity="flag",
        ))
    return results


def check_ground_transport_policy(
    extracted: ExtractedReceipt,
    *,
    trip_purpose: str = "",
    trip_nights: Optional[int] = None,
    employee_grade: int = 1,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> RuleResult:
    """Ground transport: premium rideshare + airfare class rules.

    Combines rideshare-tier and flight-class checks because both share the
    "premium-only-when-justified" pattern.
    """
    cat = extracted.category or "unknown"

    # Flights
    if cat == "air_travel":
        cls = (extracted.flight_class or "").lower()
        dur = extracted.flight_duration_hours
        intl = bool(extracted.is_international)
        facts = {
            "flight_class": cls or None,
            "duration_hours": dur,
            "international": intl,
        }
        if cls == "first":
            return RuleResult(
                "first_class_prohibited", "fail",
                "First class is not reimbursable under any circumstances "
                "(TEP-005 §2.4).",
                facts=facts,
                policy_refs=["TEP-005 §2.4"],
                severity="reject",
                amount_affected=extracted.total or 0.0,
            )
        if cls == "business":
            if not intl or (dur is not None and dur < 10):
                refs = ["TEP-005 §2.3"]
                if intl:
                    refs.append("TEP-013 §2.1")
                return RuleResult(
                    "business_class_not_eligible", "fail",
                    "Business class is only permitted on international "
                    "segments ≥10h with VP approval (TEP-005 §2.3; "
                    "TEP-013 §2.1 for international travel).",
                    facts=facts,
                    policy_refs=refs,
                    severity="flag",
                )
        if cls == "premium_economy" and dur is not None and dur < 6:
            return RuleResult(
                "premium_economy_short_flight", "fail",
                "Premium economy is permitted only on segments ≥6h "
                "(TEP-005 §2.2).",
                facts=facts,
                policy_refs=["TEP-005 §2.2"],
                severity="flag",
            )
        return RuleResult(
            "ground_transport_policy", "pass",
            f"Air travel class '{cls or 'unspecified'}' meets TEP-005 §2.",
            facts=facts,
            policy_refs=["TEP-005 §2"],
        )

    # Rideshare — corpus has no rideshare-specific policy; anchor on
    # TEP-001 §3.2 (lowest practical cost).
    if cat == "ground_rideshare":
        tier_ride = (extracted.rideshare_tier or "").lower()
        facts = {"rideshare_tier": tier_ride or None}
        if tier_ride == "premium":
            return RuleResult(
                "premium_rideshare", "fail",
                "Premium rideshare tier fails the lowest-practical-cost "
                "test absent justification (TEP-001 §3.2).",
                facts=facts,
                policy_refs=["TEP-001 §3.2"],
                severity="flag",
            )
        return RuleResult(
            "ground_transport_policy", "pass",
            "Standard rideshare tier meets TEP-001 §3.2 (Reasonableness).",
            facts=facts,
            policy_refs=["TEP-001 §3.2"],
        )

    if cat in {"ground_taxi", "ground_parking", "ground_transit"}:
        return RuleResult(
            "ground_transport_policy", "pass",
            f"{cat.replace('_', ' ').title()} is reimbursable when reasonable "
            "(TEP-001 §3.2).",
            facts={"category": cat},
            policy_refs=["TEP-001 §3.2"],
        )

    return RuleResult(
        "ground_transport_policy", "unknown",
        "Not a transport category.",
        facts={"category": cat},
        policy_refs=["TEP-001 §3.2"],
    )


def check_conference_meal_overlap(
    extracted: ExtractedReceipt,
    *,
    trip_purpose: str = "",
    trip_nights: Optional[int] = None,
    employee_grade: int = 1,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> RuleResult:
    """Meals provided by a same-day conference are not separately reimbursable.

    Looks across `sibling_receipts` for a `conference_registration` on the
    same date. If found, the current meal claim is flagged for human review
    (we don't auto-reject because the conference may not actually have
    included that specific meal).
    """
    cat = extracted.category or "unknown"
    if cat not in MEAL_CATEGORIES:
        return RuleResult(
            "conference_meal_overlap", "unknown",
            "Rule only applies to meal receipts.",
            facts={"category": cat},
            policy_refs=["TEP-001 §3.1"],
        )
    if not sibling_receipts:
        return RuleResult(
            "conference_meal_overlap", "pass",
            "No sibling receipts to compare against.",
            facts={},
            policy_refs=["TEP-001 §3.1"],
        )
    date = extracted.transaction_date
    if not date:
        return RuleResult(
            "conference_meal_overlap", "unknown",
            "Receipt has no transaction date — cannot check overlap.",
            facts={},
            policy_refs=["TEP-001 §3.1"],
        )
    conf = next(
        (
            s for s in sibling_receipts
            if (s.category or "") == "conference_registration"
            and (s.transaction_date or "") == date
        ),
        None,
    )
    if conf is None:
        return RuleResult(
            "conference_meal_overlap", "pass",
            "No same-day conference registration found.",
            facts={"date": date},
            policy_refs=["TEP-001 §3.1"],
        )
    return RuleResult(
        "conference_meal_overlap", "fail",
        f"Same-day conference registration '{conf.merchant or 'unknown'}' "
        f"may already cover this {cat.replace('meal_', '')} — a separate "
        "claim risks duplicating the business purpose (TEP-001 §3.1).",
        facts={
            "date": date,
            "conference_merchant": conf.merchant,
            "conference_total": conf.total,
        },
        policy_refs=["TEP-001 §3.1"],
        severity="flag",
    )


def check_receipt_itemization(
    extracted: ExtractedReceipt,
    *,
    trip_purpose: str = "",
    trip_nights: Optional[int] = None,
    employee_grade: int = 1,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> list[RuleResult]:
    """Itemization + receipt-mismatch rules.

    TEP-001 §3.3 (Documentation) requires "original itemized receipts".
    The <$25 waiver is an internal Finance-Ops practice not in the corpus;
    it is surfaced as `pass`/info anchored on TEP-001 §3.3 so reviewers
    can apply judgement. Mismatch additionally cites COC-001 §2.2 which
    prohibits falsification of expense reports.
    """
    results: list[RuleResult] = []
    cat = extracted.category or "unknown"
    total = extracted.total or 0.0

    # 1. <$25 receipt waiver (non-meal). Internal practice, not in corpus.
    if total and total < 25 and cat not in MEAL_CATEGORIES:
        results.append(RuleResult(
            "under_25_receipt_waiver", "pass",
            "Receipt under $25; internal practice permits summary "
            "documentation. Itemization rule lives in TEP-001 §3.3.",
            facts={"total": total, "category": cat},
            policy_refs=["TEP-001 §3.3"],
        ))

    # 2. Itemization requirement (meals + lodging + conference)
    needs_items = cat in MEAL_CATEGORIES or cat in {"lodging", "conference_registration"}
    if needs_items and total:
        has_breakdown = bool(extracted.line_items) or bool(extracted.subtotal)
        if not has_breakdown or "missing_itemization" in (extracted.extraction_issues or []):
            results.append(RuleResult(
                "missing_itemization", "fail",
                f"{cat.replace('_', ' ').title()} receipt lacks the itemized "
                "breakdown required by TEP-001 §3.3 (Documentation).",
                facts={
                    "category": cat,
                    "has_line_items": bool(extracted.line_items),
                    "has_subtotal": extracted.subtotal is not None,
                },
                policy_refs=["TEP-001 §3.3"],
                severity="flag",
            ))
        else:
            results.append(RuleResult(
                "missing_itemization", "pass",
                "Receipt shows the itemization required by TEP-001 §3.3.",
                facts={"category": cat},
                policy_refs=["TEP-001 §3.3"],
            ))

    # 3. Claimed-vs-total mismatch — cites Documentation + Code of Conduct
    # falsification clause.
    if extracted.claimed_amount is not None and total:
        diff = abs(extracted.claimed_amount - total)
        facts = {
            "claimed": extracted.claimed_amount,
            "receipt_total": total,
            "diff": round(diff, 2),
        }
        if diff > 1.0:
            results.append(RuleResult(
                "receipt_amount_mismatch", "fail",
                f"Claimed ${extracted.claimed_amount:.2f} ≠ receipt total "
                f"${total:.2f} (diff ${diff:.2f}); the lesser is reimbursable "
                "absent a documented explanation (TEP-001 §3.3; COC-001 §2.2 "
                "prohibits falsification of expense reports).",
                facts=facts,
                policy_refs=["TEP-001 §3.3", "COC-001 §2.2"],
                severity="flag",
                amount_affected=round(diff, 2),
            ))
        else:
            results.append(RuleResult(
                "receipt_amount_mismatch", "pass",
                "Claimed amount matches receipt total within $1.",
                facts=facts,
                policy_refs=["TEP-001 §3.3"],
            ))

    if not results:
        results.append(RuleResult(
            "receipt_itemization", "unknown",
            "No itemization sub-rules applicable to this receipt.",
            facts={"category": cat, "total": total},
            policy_refs=["TEP-001 §3.3"],
        ))
    return results


# ============================================================
# 4. Per-receipt orchestrator
# ============================================================

# Order matters for readability of findings in the UI.
_PER_RECEIPT_RULES = (
    check_meal_cap,
    check_tip_cap,
    check_alcohol_policy,
    check_lodging_cap,
    check_ground_transport_policy,
    check_conference_meal_overlap,
    check_receipt_itemization,
)


def evaluate_rules(
    extracted: ExtractedReceipt,
    *,
    trip_purpose: str = "",
    trip_nights: Optional[int] = None,
    employee_grade: int = 1,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> list[RuleResult]:
    """Run every per-receipt rule and return every RuleResult (pass / fail /
    unknown). Useful for evals and the explainability panel."""
    out: list[RuleResult] = []
    for fn in _PER_RECEIPT_RULES:
        res = fn(
            extracted,
            trip_purpose=trip_purpose,
            trip_nights=trip_nights,
            employee_grade=employee_grade,
            sibling_receipts=sibling_receipts,
        )
        if isinstance(res, list):
            out.extend(res)
        else:
            out.append(res)
    return out


def run_rules(
    extracted: ExtractedReceipt,
    trip_purpose: str,
    trip_nights: Optional[int],
    employee_grade: int,
    sibling_receipts: list[ExtractedReceipt] | None = None,
) -> list[DeterministicFinding]:
    """Legacy entry point used by the pipeline. Returns only `fail` results
    as `DeterministicFinding`s (the shape the adjudicator + DB expect)."""
    results = evaluate_rules(
        extracted,
        trip_purpose=trip_purpose,
        trip_nights=trip_nights,
        employee_grade=employee_grade,
        sibling_receipts=sibling_receipts,
    )
    findings: list[DeterministicFinding] = []
    for r in results:
        f = r.to_finding()
        if f is not None:
            findings.append(f)
    return findings


# ============================================================
# 5. Submission-level rule
# ============================================================

def check_submission_approval_threshold(
    total_claimed: float,
    *,
    employee_grade: int = 1,
    receipt_count: int = 0,
) -> RuleResult:
    """Submissions over $5,000 require VP sign-off (TEP-001 §4.3).

    Returns `fail` (severity flag) when the threshold is crossed so the
    submission rollup can surface a required approver. Does not block
    individual receipt verdicts. TEP-009 §2 defines which grade holds VP
    approval authority.
    """
    facts = {
        "total_claimed": total_claimed,
        "threshold": SUBMISSION_VP_THRESHOLD,
        "employee_grade": employee_grade,
        "receipt_count": receipt_count,
    }
    if total_claimed > SUBMISSION_VP_THRESHOLD:
        return RuleResult(
            "submission_over_vp_threshold", "fail",
            f"Submission total ${total_claimed:,.2f} exceeds the "
            f"${SUBMISSION_VP_THRESHOLD:,.0f} VP-approval threshold "
            "(TEP-001 §4.3; TEP-009 §2 defines VP authority).",
            facts=facts,
            policy_refs=["TEP-001 §4.3", "TEP-009 §2"],
            severity="flag",
        )
    return RuleResult(
        "submission_over_vp_threshold", "pass",
        f"Submission total ${total_claimed:,.2f} is within manager or "
        "director approval authority (TEP-001 §4.1–4.2).",
        facts=facts,
        policy_refs=["TEP-001 §4.1", "TEP-001 §4.2"],
    )


# ============================================================
# 6. Verdict arithmetic (legacy)
# ============================================================

def deterministic_verdict(
    findings: list[DeterministicFinding], extracted: ExtractedReceipt
) -> tuple[str, float, float]:
    """Return (preliminary_verdict, reimbursable, non_reimbursable) from rules only."""
    total = extracted.total or 0.0
    non_reimb = 0.0
    has_reject = False
    has_flag = False
    for f in findings:
        if f.severity == "reject":
            has_reject = True
            non_reimb += f.amount_affected or 0.0
        elif f.severity == "flag":
            has_flag = True
            non_reimb += f.amount_affected or 0.0
    non_reimb = min(round(non_reimb, 2), total)
    reimb = round(total - non_reimb, 2)
    if has_reject and non_reimb >= total - 0.01:
        return "rejected", 0.0, total
    if has_reject:
        return "flagged", reimb, non_reimb
    if has_flag:
        return "flagged", reimb, non_reimb
    return "compliant", total, 0.0

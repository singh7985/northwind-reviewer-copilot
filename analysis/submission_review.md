# Submission review notes (manual, pre-implementation)

These notes were produced by reading every receipt PDF by hand and
cross-referencing the in-corpus policy clauses. They are deliberately written
*before* trusting any output of the system, so they serve as the human
reference for [golden_truth.json](golden_truth.json) and the
[eval harness](../eval/harness.py).

A note on caps used below: meal / lodging / alcohol caps **are not defined in
the in-corpus policies** (TEP-002/003/004 are referenced but absent). The
numeric caps below are the finance-team conventions hard-coded in
[deterministic.py](../backend/app/services/deterministic.py): meals
$25/$35/$75, lodging $350/$250/$175, Tier-1 uplift 1.25×, tip-flag >20 %.
See [PHASE2_FINDINGS.md](PHASE2_FINDINGS.md) for the rationale and the
remediation plan.

---

## 01 — clean_denver  (Sarah Chen · Grade 5 · 2 nights · client visit)

| Receipt | $ | Read | Verdict |
|---|---:|---|---|
| 01 United LAX↔DEN economy | 324.20 | Economy domestic — TEP-005 §2.1 default. | **compliant** |
| 02 Marriott Denver (2 nights) | 470.00 | $235/night room. Denver = Tier 2, cap $250. | **compliant** |
| 03 Uber DEN→hotel | 42.18 | Tip $4.60 / $37.58 = 12.2 %. | **compliant** |
| 04 Dinner Mercantile (solo) | 58.40 | Under $75 dinner cap. | **compliant** |
| 05 Breakfast Snooze | 17.85 | Under $25 cap; tip is $0.03 (rounding artifact). | **compliant** |
| 06 Lunch Sushi Den | 24.10 | Under $35 cap. | **compliant** |
| 07 Dinner Avanti (solo) | 52.30 | Under $75 cap. | **compliant** |
| 08 Uber hotel→DEN | 38.40 | Cash-tip note; receipt-line tip is 0. | **compliant** |

**Submission verdict:** `ready_to_approve`. Totals: claimed 1027.43, reimbursable 1027.43.

This is the control case — if the system flags anything here, the cap math is wrong.

---

## 02 — clean_boston_conf  (Marcus Rivera · Grade 7 · 3 nights · AWS re:Inforce)

| Receipt | $ | Read | Verdict |
|---|---:|---|---|
| 01 Conference registration | 1,895.00 | Conference attendance fee + $100 workshop. TEP-014 not in corpus, but TEP-001 §3.1 business-purpose principle clearly satisfied. Amount triggers Director approval per TEP-001 §4.2, which Marcus is (Grade 7). | **compliant** |
| 02 Delta LAX↔BOS Premium Select | 487.30 | Outbound 6h48m, return 6h27m. TEP-005 §2.2: premium economy permitted on segments ≥6h. Both segments qualify. | **compliant** |
| 03 Hilton Back Bay (3 nights) | 834.00 | $278/night room (post-tax). Boston = Tier 1, cap $350. | **compliant** |
| 04 Lyft BOS→hotel | 54.20 | Tip $7.39 on $36.94 = 20.0 % (at the threshold, not over). | **compliant** |
| 05 Lunch Saltie Girl | 31.85 | Under $35 lunch cap. | **compliant** |
| 06 Dinner Eastern Standard | 67.50 | Receipt note: 3 NW employees, **split check, each paid own meal, no external clients**. Per-person ticket = $67.50, under $75. No client-entertainment overlay needed because there were none. | **compliant** |
| 07 Lyft hotel→BOS | 48.10 | Tip $6.00 on $30.02 = 20.0 %. | **compliant** |

**Submission verdict:** `ready_to_approve`. The only thing a reviewer might
twitch at — Premium Select airfare — is explicitly *allowed* by the policy and
the system should not flag it.

---

## 03 — dinner_over_cap  (Priya Patel · Grade 4 · 1 night · vendor visit Chicago)

| Receipt | $ | Read | Verdict |
|---|---:|---|---|
| 01 American LAX↔ORD economy | 385.60 | Economy domestic. | **compliant** |
| 02 Hyatt Regency Chicago (1 night) | 245.00 | $215/night room. Chicago = Tier 2, cap $250. | **compliant** |
| 03 Uber ORD→hotel | 38.50 | Tip 15 %. | **compliant** |
| 04 **Dinner Alinea (solo)** | **148.20** | $138.57 + $9.63 tip. Dinner cap $75. **Receipt note: "Solo diner. No external attendees."** No client entertainment justification possible. Roughly 2× the cap. | **flagged** — `meal_cap_exceeded`, severity flag. Not reject: cap exceedance is not by itself a hard-prohibition rule. |
| 05 Breakfast Wildberry | 13.80 | Under $25. | **compliant** |
| 06 Lyft hotel→ORD | 42.30 | Tip 12 %. | **compliant** |

**Submission verdict:** `needs_review`. The reviewer must decide whether a
Grade-4 Senior Specialist solo-dining at Alinea on a vendor trip warrants any
reimbursement at all (likely cap-and-reimburse to $75, the rest non-reimbursable).

**Trip-context dependency:** The trip purpose says "vendor site visit". *Could*
support a vendor entertainment story — except the receipt explicitly states
"No external attendees", so that's off the table. The system must surface
that explicit attendee note, not just the trip purpose.

---

## 04 — alcohol_solo_travel  (James Walker · Grade 6 · 2 nights · Austin)

Trip purpose: *"**Solo** carrier research trip — meeting potential regional
partners in Austin"*. The literal token `solo` triggers the solo-travel
alcohol rule.

| Receipt | $ | Read | Verdict |
|---|---:|---|---|
| 01 Southwest LAX↔AUS | 298.40 | Economy. | **compliant** |
| 02 Marriott Austin (2 nights) | 430.00 | $215/night room. Austin = Tier 2, cap $250. | **compliant** |
| 03 Uber AUS→hotel | 32.10 | Tip 20 %. | **compliant** |
| 04 Lunch Torchy's | 18.40 | Under $35. Negative "$-1.09 counter service" line is a rounding/format artifact; ignore. | **compliant** |
| 05 **Dinner Franklin BBQ (solo)** | **72.85** | Subtotal $67. **Contains 1 hefeweizen $9 + 1 Real Ale $9 + 1 glass Texas Red $8 = $26 alcohol.** Trip purpose contains "solo". Food portion $46 under $75 dinner cap — but alcohol on solo travel is the dispositive rule. | **rejected** — `alcohol_solo_travel`. Hard reject; the food portion is reimbursable but the $26 alcohol is non-reimbursable. |
| 06 Breakfast Jo's | 19.20 | Under $25. | **compliant** |
| 07 Lyft hotel→AUS | 34.50 | Tip 15 %. | **compliant** |

**Submission verdict:** `has_rejections`. Reviewer must confirm the alcohol
breakdown ($26) is the non-reimbursable amount, not the whole $72.85 dinner.

**Trip-context dependency:** Critical. Without parsing "solo" out of the trip
purpose, this is just a normal $73 dinner with beer.

---

## 05 — receipt_mismatch  (Linda Foster · Grade 5 · 2 nights · Seattle QBR)

| Receipt | $ | Read | Verdict |
|---|---:|---|---|
| 01 Alaska LAX↔SEA | 267.40 | Economy. | **compliant** |
| 02 **Hyatt Regency Seattle (2 nights)** | **914.50** | $389/night room (pre-tax). Seattle = Tier 1, cap $350. **Receipt note: "Booked outside Concur Travel tool; no corporate-rate adjustment applied. Standard public rate."** | **flagged** — `lodging_over_cap` ($39/night over cap). The off-tool booking is a secondary signal (TEP-005 §3.1 only covers flights; lodging analog lives in TEP-004 which is absent — so it cannot be cited as a hard rule, only noted under TEP-001 §3.2 reasonableness). |
| 03 Uber SEA→hotel | 28.40 | Tip 5 %. | **compliant** |
| 04 Lunch Toulouse Petit | 26.10 | Under $35. | **compliant** |
| 05 Dinner Pink Door (solo) | 54.30 | Under $75. | **compliant** |
| 06 Lyft hotel→SEA | 31.20 | Tip 3 %. | **compliant** |

**Submission verdict:** `needs_review`. The "mismatch" in the folder name
turns out to refer to the public-rate vs corporate-rate gap, not an arithmetic
mismatch — a subtler trap.

**Trip-context dependency:** Needs Seattle → Tier 1 lookup. Without it, $389
looks fine against the Tier 2 cap.

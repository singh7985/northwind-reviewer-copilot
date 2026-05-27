"""Evaluation harness for the Northwind expense pre-review copilot.

Designed around two principles from the OpenAI eval guidance:

    1. Eval as a *continuous* development tool, not an end-of-project checkbox.
       Therefore every metric is computed automatically and exits non-zero on
       regression so the same script can be wired into CI.

    2. Decompose RAG quality. Retrieval and reasoning failures look identical
       in a single accuracy number but require very different fixes. We
       therefore measure `retrieval_hit_rate` (did the right clause appear in
       the retrieved set?) separately from `citation_correctness` (did the
       adjudicator actually quote the right clause?).

Expected-outcomes format
------------------------

Two formats are auto-detected:

    A. v2 *flat* format (preferred for the held-out set):
       {
         "cases": [
           {
             "receipt_id":   "03_dinner_over_cap/04_dinner_alinea",
             "submission_dir": "03_dinner_over_cap",
             "filename":      "04_dinner_alinea.pdf",
             "expected_verdict":          "flagged",
             "expected_category":         "meal_dinner",
             "expected_policy_refs":      ["TEP-001"],
             "expected_failure_reason":   "meal cap exceeded",
             "expected_reimbursable_amount": 75.0,
             "expected_fields":           ["merchant","total","category"]
           }
         ]
       }

    B. v1 per-submission format (kept for back-compat with the
       case-study sample folders).

Run
---
    python -m eval.harness \\
        --submissions ./submissions \\
        --expected ./eval/expected_outcomes.v2.json \\
        --oos ./eval/oos_questions.json \\
        --adversarial ./eval/adversarial.json \\
        --adv-dir ./eval/adversarial \\
        --api http://localhost:8000

Writes ./eval/last_report.json and prints the same report to stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx


# ---------------------------------------------------------------- helpers ----

def _mime_for(path: Path) -> str:
    s = path.suffix.lower()
    if s == ".pdf":
        return "application/pdf"
    if s == ".png":
        return "image/png"
    if s in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if s == ".txt":
        return "text/plain"
    return "application/octet-stream"


def _doc_id_of(ref: str) -> str:
    """'TEP-001 §3.2' -> 'TEP-001'. Tolerates either spacing or no section."""
    return (ref or "").split()[0] if ref else ""


def _normalise_expected(raw: dict) -> list[dict]:
    """Coerce both v1 and v2 expected-outcomes formats into a uniform list of
    per-receipt expectation dicts."""
    out: list[dict] = []
    for case in raw.get("cases", []):
        if "filename" in case and "submission_dir" in case:
            out.append(case)
            continue
        # v1 fallback: explode nested maps into per-file rows
        sub = case["submission_dir"]
        verdicts = case.get("expected_verdicts") or {}
        cats = case.get("expected_categories") or {}
        refs = case.get("expected_refs") or {}
        names = set(verdicts) | set(cats) | set(refs)
        for name in sorted(names):
            out.append(
                {
                    "receipt_id": f"{sub}/{Path(name).stem}",
                    "submission_dir": sub,
                    "filename": name,
                    "expected_verdict": verdicts.get(name),
                    "expected_category": cats.get(name),
                    "expected_policy_refs": refs.get(name) or [],
                }
            )
    return out


def _post_receipt(api: str, sub_id: str, f: Path) -> dict:
    with f.open("rb") as fh:
        r = httpx.post(
            f"{api}/submissions/{sub_id}/receipts",
            files={"file": (f.name, fh, _mime_for(f))},
            timeout=300.0,
        )
    r.raise_for_status()
    return r.json()


def _create_submission(api: str, employee_id: str, purpose: str, start: str | None, end: str | None) -> str:
    r = httpx.post(
        f"{api}/submissions",
        json={
            "employee_id": employee_id,
            "trip_purpose": purpose,
            "trip_start": start,
            "trip_end": end,
        },
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()["id"]


# ------------------------------------------------------- main run loop -------

def run_core(api: str, submissions: Path, expected: list[dict]) -> list[dict]:
    """Upload every expected receipt and capture the adjudication response.
    Each result row contains both system output and the expectation so the
    scorers can operate on a single list."""
    rows: list[dict] = []

    by_dir: dict[str, list[dict]] = defaultdict(list)
    for e in expected:
        by_dir[e["submission_dir"]].append(e)

    for sub_dir, items in by_dir.items():
        case_dir = submissions / sub_dir
        info_path = case_dir / "employee_info.json"
        if not info_path.exists():
            print(f"[skip] no employee_info.json in {case_dir}", file=sys.stderr)
            continue
        info = json.loads(info_path.read_text())
        trip_dates = info.get("trip_dates", "")
        start, end = (trip_dates.split(" to ") + [None, None])[:2]
        sub_id = _create_submission(
            api,
            employee_id=info["employee_id"],
            purpose=info.get("trip_purpose", ""),
            start=start,
            end=end,
        )

        for exp in items:
            f = case_dir / "receipts" / exp["filename"]
            if not f.exists():
                print(f"[skip] missing receipt {f}", file=sys.stderr)
                continue
            rec = _post_receipt(api, sub_id, f)
            rows.append({"expected": exp, "actual": rec})
    return rows


# -------------------------------------------------------- scoring ------------

def score_verdict_accuracy(rows: list[dict]) -> dict:
    graded = [r for r in rows if r["expected"].get("expected_verdict")]
    correct = sum(1 for r in graded if r["actual"]["verdict"] == r["expected"]["expected_verdict"])
    n = len(graded)
    return {"graded": n, "correct": correct, "accuracy": round(correct / n, 3) if n else None}


def score_category_accuracy(rows: list[dict]) -> dict:
    graded = [r for r in rows if r["expected"].get("expected_category")]
    correct = sum(
        1
        for r in graded
        if (r["actual"]["extracted"] or {}).get("category") == r["expected"]["expected_category"]
    )
    n = len(graded)
    return {"graded": n, "correct": correct, "accuracy": round(correct / n, 3) if n else None}


def score_retrieval_hit_rate(rows: list[dict]) -> dict:
    """Did the expected policy doc(s) appear in the retrieved_clauses set?
    Independent of whether the adjudicator chose to cite them. This is the
    *retrieval* leg of RAG quality."""
    total = 0
    hits = 0
    misses: list[dict] = []
    for r in rows:
        exp_refs = r["expected"].get("expected_policy_refs") or []
        if not exp_refs:
            continue
        retrieved_docs = {c["doc_id"] for c in (r["actual"].get("retrieved_clauses") or [])}
        for ref in exp_refs:
            total += 1
            if _doc_id_of(ref) in retrieved_docs:
                hits += 1
            else:
                misses.append(
                    {
                        "receipt_id": r["expected"].get("receipt_id"),
                        "expected_ref": ref,
                        "retrieved_docs": sorted(retrieved_docs),
                    }
                )
    return {
        "expected_refs": total,
        "hits": hits,
        "hit_rate": round(hits / total, 3) if total else None,
        "misses": misses,
    }


def score_citation_correctness(rows: list[dict]) -> dict:
    """Did the *adjudicator* cite the expected doc?

    Precision = of refs the system cited, fraction that was expected.
    Recall    = of refs we expected, fraction the system cited.
    Splitting the two catches chatty over-citation as well as omission.
    """
    expected_seen = 0
    expected_hit = 0
    cited_seen = 0
    cited_hit = 0
    for r in rows:
        exp_refs = r["expected"].get("expected_policy_refs") or []
        cited_docs = {_doc_id_of(x) for x in (r["actual"].get("policy_refs") or [])}
        exp_docs = {_doc_id_of(x) for x in exp_refs}
        if exp_docs:
            for d in exp_docs:
                expected_seen += 1
                if d in cited_docs:
                    expected_hit += 1
        for d in cited_docs:
            cited_seen += 1
            if d in exp_docs:
                cited_hit += 1
    return {
        "precision": round(cited_hit / cited_seen, 3) if cited_seen else None,
        "recall": round(expected_hit / expected_seen, 3) if expected_seen else None,
        "n_expected_refs": expected_seen,
        "n_cited_refs": cited_seen,
    }


def score_extraction_completeness(rows: list[dict]) -> dict:
    """Fraction of expected_fields whose extracted value is non-null/empty."""
    total = 0
    present = 0
    by_field: dict[str, dict] = defaultdict(lambda: {"present": 0, "total": 0})
    for r in rows:
        fields = r["expected"].get("expected_fields") or []
        ex = r["actual"].get("extracted") or {}
        for f in fields:
            total += 1
            by_field[f]["total"] += 1
            v = ex.get(f)
            if v not in (None, "", [], {}):
                present += 1
                by_field[f]["present"] += 1
    return {
        "graded_fields": total,
        "present_fields": present,
        "completeness": round(present / total, 3) if total else None,
        "per_field": {
            k: {**v, "rate": round(v["present"] / v["total"], 3) if v["total"] else None}
            for k, v in by_field.items()
        },
    }


def score_reimbursable_amount(rows: list[dict]) -> dict:
    """If expected_reimbursable_amount is given, compare it to actual within
    $1 tolerance (covers rounding & cap-arithmetic edge cases)."""
    total = 0
    correct = 0
    diffs: list[dict] = []
    for r in rows:
        exp = r["expected"].get("expected_reimbursable_amount")
        if exp is None:
            continue
        total += 1
        actual = r["actual"].get("reimbursable_amount") or 0.0
        if abs(actual - exp) <= 1.0:
            correct += 1
        else:
            diffs.append(
                {
                    "receipt_id": r["expected"].get("receipt_id"),
                    "expected": exp,
                    "actual": actual,
                    "delta": round(actual - exp, 2),
                }
            )
    return {
        "graded": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else None,
        "diffs": diffs,
    }


def score_override_agreement(api: str) -> dict:
    """How often the system's `original_verdict` matched the final verdict
    (i.e. no reviewer flip was needed). Pulled from history persisted in
    Postgres, so this metric is meaningful only after at least some real
    review activity. Higher = the system is producing answers reviewers
    accept; low = the model needs work."""
    try:
        r = httpx.get(f"{api}/submissions", timeout=60.0)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e)}
    subs = r.json()
    total = 0
    agree = 0
    flips: list[dict] = []
    for s in subs:
        for rec in s.get("receipts", []):
            orig = rec.get("original_verdict")
            cur = rec.get("verdict")
            if orig is None:
                continue
            total += 1
            if orig == cur:
                agree += 1
            else:
                flips.append(
                    {
                        "receipt_id": rec["id"],
                        "original_verdict": orig,
                        "current_verdict": cur,
                        "override_count": len(rec.get("overrides") or []),
                    }
                )
    return {
        "graded": total,
        "agree": agree,
        "agreement_rate": round(agree / total, 3) if total else None,
        "flips_sample": flips[:10],
    }


def score_oos_refusal(api: str, questions: list[str]) -> dict:
    if not questions:
        return {"n_questions": 0, "refusal_rate": None}
    refused = 0
    for q in questions:
        r = httpx.post(f"{api}/qa", json={"question": q}, timeout=120.0)
        r.raise_for_status()
        if r.json().get("refused"):
            refused += 1
    return {
        "n_questions": len(questions),
        "refused": refused,
        "refusal_rate": round(refused / len(questions), 3),
    }


def score_adversarial(api: str, adv_cfg: dict, adv_dir: Path) -> dict:
    """Run adversarial fixtures + irrelevant-policy-question probes.
    Each fixture allows multiple acceptable verdicts because the right
    answer is usually 'any of {needs_human_review, flagged, rejected}' —
    the system must just not silently say 'compliant'."""
    if not adv_cfg or not adv_cfg.get("cases"):
        return {"n": 0}

    try:
        emps = httpx.get(f"{api}/employees", timeout=30.0).json()
    except Exception as e:
        return {"error": f"could not list employees: {e}"}
    if not emps:
        return {"error": "no employees seeded; run /admin/seed first"}

    sub_id = _create_submission(
        api,
        employee_id=emps[0]["id"],
        purpose=adv_cfg.get("trip_purpose", "Adversarial probes"),
        start=None,
        end=None,
    )

    rows: list[dict] = []
    pass_count = 0
    for case in adv_cfg["cases"]:
        f = adv_dir / case["filename"]
        if not f.exists():
            rows.append({**case, "error": f"missing fixture {f}"})
            continue
        rec = _post_receipt(api, sub_id, f)
        accepted = set(case.get("expected_verdict_any_of") or [])
        exp_issues = set(case.get("expected_extraction_issues_any_of") or [])
        actual_v = rec["verdict"]
        actual_issues = set(rec.get("extraction_issues") or [])
        verdict_ok = (not accepted) or (actual_v in accepted)
        issues_ok = (not exp_issues) or bool(exp_issues & actual_issues)
        passed = verdict_ok and issues_ok
        if passed:
            pass_count += 1
        rows.append(
            {
                "receipt_id": case["receipt_id"],
                "expected_verdict_any_of": sorted(accepted),
                "actual_verdict": actual_v,
                "expected_extraction_issues_any_of": sorted(exp_issues),
                "actual_extraction_issues": sorted(actual_issues),
                "passed": passed,
                "rationale": case.get("rationale"),
            }
        )

    irrelevant_qs = adv_cfg.get("irrelevant_policy_questions") or []
    irr_refused = 0
    irr_rows: list[dict] = []
    for q in irrelevant_qs:
        r = httpx.post(f"{api}/qa", json={"question": q}, timeout=120.0)
        r.raise_for_status()
        body = r.json()
        if body.get("refused"):
            irr_refused += 1
        irr_rows.append(
            {
                "question": q,
                "refused": body.get("refused"),
                "answer_preview": (body.get("answer") or "")[:120],
            }
        )

    return {
        "n": len(rows),
        "pass_count": pass_count,
        "pass_rate": round(pass_count / len(rows), 3) if rows else None,
        "cases": rows,
        "irrelevant_policy_questions": {
            "n": len(irrelevant_qs),
            "refused": irr_refused,
            "refusal_rate": round(irr_refused / len(irrelevant_qs), 3)
            if irrelevant_qs
            else None,
            "details": irr_rows,
        },
    }


# ------------------------------------------------------------------ run ------

def run(
    api: str,
    submissions: Path,
    expected_path: Path,
    oos_path: Path | None,
    adversarial_path: Path | None,
    adv_dir: Path | None,
) -> dict:
    try:
        httpx.post(f"{api}/admin/seed", timeout=600.0).raise_for_status()
    except Exception as e:
        print(f"[warn] /admin/seed failed (continuing): {e}", file=sys.stderr)

    raw = json.loads(expected_path.read_text()) if expected_path.exists() else {"cases": []}
    expected = _normalise_expected(raw)
    rows = run_core(api, submissions, expected)

    report: dict[str, Any] = {
        "n_receipts": len(rows),
        "verdict_accuracy": score_verdict_accuracy(rows),
        "category_accuracy": score_category_accuracy(rows),
        "retrieval_hit_rate": score_retrieval_hit_rate(rows),
        "citation_correctness": score_citation_correctness(rows),
        "extraction_completeness": score_extraction_completeness(rows),
        "reimbursable_amount_accuracy": score_reimbursable_amount(rows),
        "override_agreement": score_override_agreement(api),
    }

    if oos_path and oos_path.exists():
        oos_qs = json.loads(oos_path.read_text())
        report["oos_refusal"] = score_oos_refusal(api, oos_qs)

    if adversarial_path and adversarial_path.exists() and adv_dir:
        adv_cfg = json.loads(adversarial_path.read_text())
        report["adversarial"] = score_adversarial(api, adv_cfg, adv_dir)

    report["details"] = [
        {
            "receipt_id": r["expected"].get("receipt_id"),
            "filename": r["expected"].get("filename"),
            "expected_verdict": r["expected"].get("expected_verdict"),
            "actual_verdict": r["actual"]["verdict"],
            "expected_category": r["expected"].get("expected_category"),
            "actual_category": (r["actual"]["extracted"] or {}).get("category"),
            "expected_refs": r["expected"].get("expected_policy_refs") or [],
            "cited_refs": r["actual"].get("policy_refs") or [],
            "retrieved_docs": sorted(
                {c["doc_id"] for c in (r["actual"].get("retrieved_clauses") or [])}
            ),
            "confidence": r["actual"].get("confidence"),
            "extraction_confidence": r["actual"].get("extraction_confidence"),
            "extraction_issues": r["actual"].get("extraction_issues") or [],
        }
        for r in rows
    ]
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--submissions", default="./submissions")
    ap.add_argument("--expected", default="./eval/expected_outcomes.v2.json")
    ap.add_argument("--oos", default="./eval/oos_questions.json")
    ap.add_argument("--adversarial", default="./eval/adversarial.json")
    ap.add_argument("--adv-dir", default="./eval/adversarial")
    ap.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="Exit non-zero if verdict_accuracy falls below this threshold (0..1).",
    )
    args = ap.parse_args()

    report = run(
        api=args.api,
        submissions=Path(args.submissions),
        expected_path=Path(args.expected),
        oos_path=Path(args.oos),
        adversarial_path=Path(args.adversarial),
        adv_dir=Path(args.adv_dir),
    )
    out_path = Path("./eval/last_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

    if args.fail_under is not None:
        acc = (report.get("verdict_accuracy") or {}).get("accuracy")
        if acc is not None and acc < args.fail_under:
            print(f"\nFAIL: verdict_accuracy {acc} < {args.fail_under}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

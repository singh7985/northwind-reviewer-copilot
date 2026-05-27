"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { VerdictBadge } from "@/components/VerdictBadge";
import { ConfidenceBadge } from "@/components/ConfidenceBadge";

type UploadStatus = {
  filename: string;
  state: "queued" | "uploading" | "done" | "error";
  message?: string;
};

export default function SubmissionPage() {
  const { id } = useParams<{ id: string }>();
  const [sub, setSub] = useState<any>(null);
  const [uploads, setUploads] = useState<UploadStatus[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setSub(await api.getSubmission(id));
    } catch (e: any) {
      setError(e.message);
    }
  }
  useEffect(() => {
    if (id) refresh();
  }, [id]);

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    const initial: UploadStatus[] = files.map((f) => ({
      filename: f.name,
      state: "queued",
    }));
    setUploads((prev) => [...initial, ...prev]);
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      setUploads((prev) =>
        prev.map((u, idx) =>
          idx === i && u.filename === f.name ? { ...u, state: "uploading" } : u
        )
      );
      try {
        await api.uploadReceipt(id, f);
        setUploads((prev) =>
          prev.map((u, idx) =>
            idx === i && u.filename === f.name ? { ...u, state: "done" } : u
          )
        );
      } catch (err: any) {
        setUploads((prev) =>
          prev.map((u, idx) =>
            idx === i && u.filename === f.name
              ? { ...u, state: "error", message: String(err?.message || err) }
              : u
          )
        );
      }
    }
    await refresh();
    e.target.value = "";
  }

  const inFlight = uploads.some((u) => u.state === "uploading" || u.state === "queued");

  if (!sub) return <div className="text-slate-500 text-sm">Loading…</div>;

  return (
    <div className="space-y-6">
      <div className="bg-white border rounded-lg p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase text-slate-500">Submission</div>
            <h1 className="text-lg font-semibold">{sub.trip_purpose}</h1>
            <div className="text-sm text-slate-600 mt-1">
              {sub.employee_name} · {sub.employee_id} ·{" "}
              {sub.trip_start && sub.trip_end ? `${sub.trip_start} → ${sub.trip_end}` : "no dates"}
            </div>
          </div>
          <div className="text-right text-sm">
            <div>Status: <strong>{sub.status}</strong></div>
            <div className="mt-1 space-x-3">
              <span>Claimed <strong>${sub.total_claimed?.toFixed(2)}</strong></span>
              <span className="text-emerald-700">
                Reimbursable <strong>${sub.total_reimbursable?.toFixed(2)}</strong>
              </span>
              <span className="text-rose-700">
                Non-reimbursable <strong>${sub.total_non_reimbursable?.toFixed(2)}</strong>
              </span>
            </div>
            <div className="mt-1 text-xs text-slate-500">
              {Object.entries(sub.counts || {})
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
            </div>
          </div>
        </div>

        <div className="mt-4 flex items-center gap-3">
          <label className="inline-flex items-center px-3 py-2 rounded bg-slate-900 text-white text-sm cursor-pointer">
            {inFlight ? "Uploading…" : "Upload receipts (PDF / JPG / PNG / TXT)"}
            <input
              type="file"
              multiple
              accept=".pdf,.jpg,.jpeg,.png,.txt,application/pdf,image/*,text/plain"
              onChange={onUpload}
              className="hidden"
            />
          </label>
          <button onClick={refresh} className="text-sm text-slate-600 underline">
            Refresh
          </button>
          {uploads.length > 0 && (
            <button
              onClick={() => setUploads([])}
              className="text-xs text-slate-500 underline"
            >
              Clear upload log
            </button>
          )}
        </div>
        {error && <div className="mt-3 text-sm text-rose-700">{error}</div>}

        {uploads.length > 0 && (
          <ul className="mt-3 space-y-1 text-xs">
            {uploads.map((u, i) => (
              <li key={i} className="flex items-center gap-2">
                <span
                  className={
                    u.state === "done"
                      ? "inline-block w-2 h-2 rounded-full bg-emerald-500"
                      : u.state === "error"
                      ? "inline-block w-2 h-2 rounded-full bg-rose-500"
                      : u.state === "uploading"
                      ? "inline-block w-2 h-2 rounded-full bg-amber-500 animate-pulse"
                      : "inline-block w-2 h-2 rounded-full bg-slate-300"
                  }
                />
                <span className="font-mono text-slate-700">{u.filename}</span>
                <span className="text-slate-500">
                  {u.state === "done"
                    ? "processed"
                    : u.state === "error"
                    ? `failed — ${u.message}`
                    : u.state}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-4">
        {sub.receipts.length === 0 && (
          <div className="text-sm text-slate-500">No receipts yet — upload to begin.</div>
        )}
        {sub.receipts.map((r: any) => (
          <ReceiptCard key={r.id} receipt={r} onChanged={refresh} />
        ))}
      </div>
    </div>
  );
}

function ReceiptCard({ receipt, onChanged }: { receipt: any; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [overriding, setOverriding] = useState(false);
  const [newVerdict, setNewVerdict] = useState(receipt.verdict);
  const [comment, setComment] = useState("");
  const [reviewer, setReviewer] = useState("reviewer@northwind");
  const e = receipt.extracted || {};

  async function submitOverride() {
    if (!comment.trim() || comment.trim().length < 3) return;
    setOverriding(true);
    try {
      await api.override(receipt.id, {
        reviewer,
        new_verdict: newVerdict,
        comment,
      });
      setComment("");
      onChanged();
    } finally {
      setOverriding(false);
    }
  }

  return (
    <div className="bg-white border rounded-lg">
      <div className="p-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <VerdictBadge verdict={receipt.verdict} />
            <ConfidenceBadge value={receipt.confidence || 0} label="adjudication" />
            <span className="text-sm font-medium">{receipt.filename}</span>
            <span className="text-xs text-slate-500">
              · {e.category || "unknown"}
            </span>
            {receipt.overrides?.length ? (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-violet-100 text-violet-800 ring-1 ring-violet-300">
                {receipt.overrides.length} override
                {receipt.overrides.length > 1 ? "s" : ""}
              </span>
            ) : null}
          </div>
          {receipt.original_verdict &&
            receipt.original_verdict !== receipt.verdict && (
              <div className="mt-1 text-xs text-slate-500 flex items-center gap-2">
                <span className="uppercase tracking-wide">Originally</span>
                <VerdictBadge verdict={receipt.original_verdict} />
                {typeof receipt.original_confidence === "number" && (
                  <ConfidenceBadge
                    value={receipt.original_confidence}
                    label="orig"
                  />
                )}
              </div>
            )}
          <div className="text-sm text-slate-700 mt-1">{receipt.rationale}</div>
          <div className="text-xs text-slate-500 mt-1">
            Merchant: {e.merchant || "?"} · Date: {e.transaction_date || "?"} · Total: $
            {(e.total ?? 0).toFixed?.(2) ?? e.total} · Reimb $
            {(receipt.reimbursable_amount ?? 0).toFixed(2)} · Non-reimb $
            {(receipt.non_reimbursable_amount ?? 0).toFixed(2)}
          </div>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="text-sm text-blue-700 underline"
        >
          {open ? "Hide why" : "Show why →"}
        </button>
      </div>

      {open && (
        <div className="border-t bg-slate-50 p-4 space-y-4 text-sm">
          {receipt.ambiguity_reason && (
            <div className="rounded bg-blue-50 border border-blue-200 px-3 py-2">
              <strong>Ambiguity:</strong> {receipt.ambiguity_reason}
            </div>
          )}

          <div>
            <div className="text-xs uppercase text-slate-500 mb-1">
              Deterministic findings
            </div>
            {receipt.deterministic_findings?.length === 0 ? (
              <div className="text-slate-500 text-xs">None.</div>
            ) : (
              <ul className="space-y-1">
                {receipt.deterministic_findings.map((f: any, i: number) => (
                  <li key={i} className="border-l-2 pl-2 border-slate-300">
                    <span
                      className={
                        f.severity === "reject"
                          ? "text-rose-700"
                          : f.severity === "flag"
                          ? "text-amber-700"
                          : "text-slate-600"
                      }
                    >
                      [{f.rule_id}]
                    </span>{" "}
                    {f.message}{" "}
                    {f.policy_refs?.length ? (
                      <span className="text-xs text-slate-500">
                        ({f.policy_refs.join(", ")})
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div>
            <div className="text-xs uppercase text-slate-500 mb-1">
              Policy quotes (the system relied on these)
            </div>
            {receipt.policy_quotes?.length === 0 ? (
              <div className="text-slate-500 text-xs">No clauses quoted.</div>
            ) : (
              <ul className="space-y-2">
                {receipt.policy_quotes.map((q: any, i: number) => (
                  <li key={i} className="rounded bg-white border p-2">
                    <div className="text-xs text-slate-500 mb-1">
                      {q.doc_id} §{q.section}
                    </div>
                    <div className="text-sm">"{q.quote}"</div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <details className="text-xs">
            <summary className="cursor-pointer text-slate-600">
              Extracted fields
              <span className="ml-2">
                <ConfidenceBadge
                  value={receipt.extraction_confidence || 0}
                  label="extraction"
                />
              </span>
              {receipt.extraction_issues?.length ? (
                <span className="ml-2 text-amber-700">
                  issues: {receipt.extraction_issues.join(", ")}
                </span>
              ) : null}
            </summary>
            <pre className="mt-2 bg-white border rounded p-2 overflow-auto">
              {JSON.stringify(receipt.extracted, null, 2)}
            </pre>
          </details>

          <details className="text-xs">
            <summary className="cursor-pointer text-slate-600">
              Retrieved clauses ({receipt.retrieved_clauses?.length || 0})
            </summary>
            <ul className="mt-2 space-y-1">
              {receipt.retrieved_clauses?.map((c: any, i: number) => (
                <li key={i} className="bg-white border rounded p-2">
                  <span className="text-slate-500">{c.doc_id} §{c.section}</span>{" "}
                  {c.text.slice(0, 300)}…
                </li>
              ))}
            </ul>
          </details>

          <div className="border-t pt-3">
            <div className="text-xs uppercase text-slate-500 mb-1">Reviewer override</div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={reviewer}
                onChange={(ev) => setReviewer(ev.target.value)}
                className="border rounded px-2 py-1 text-sm w-56"
                placeholder="reviewer name / email"
              />
              <select
                value={newVerdict}
                onChange={(ev) => setNewVerdict(ev.target.value)}
                className="border rounded px-2 py-1 text-sm"
              >
                <option value="compliant">compliant</option>
                <option value="flagged">flagged</option>
                <option value="rejected">rejected</option>
                <option value="needs_human_review">needs_human_review</option>
              </select>
              <input
                value={comment}
                onChange={(ev) => setComment(ev.target.value)}
                className="border rounded px-2 py-1 text-sm flex-1 min-w-[200px]"
                placeholder="Mandatory reviewer comment"
              />
              <button
                onClick={submitOverride}
                disabled={overriding || comment.trim().length < 3}
                className="rounded bg-slate-900 text-white text-sm px-3 py-1 disabled:opacity-50"
              >
                {overriding ? "Saving…" : "Override"}
              </button>
            </div>
            {receipt.overrides?.length ? (
              <ul className="mt-3 text-xs space-y-1">
                <li className="text-slate-500 uppercase text-[10px] tracking-wide">
                  Override trail
                </li>
                {receipt.original_verdict && receipt.original_rationale && (
                  <li className="text-slate-600 border-l-2 border-slate-300 pl-2">
                    <strong>system (original)</strong>:{" "}
                    <span className="font-mono">{receipt.original_verdict}</span>
                    <div className="text-slate-500">
                      "{receipt.original_rationale}"
                    </div>
                  </li>
                )}
                {receipt.overrides.map((o: any) => (
                  <li key={o.id} className="text-slate-600">
                    <strong>{o.reviewer}</strong> changed{" "}
                    <span className="font-mono">{o.previous_verdict}</span> →{" "}
                    <span className="font-mono">{o.new_verdict}</span>: "{o.comment}"{" "}
                    <span className="text-slate-400">
                      {new Date(o.created_at).toLocaleString()}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

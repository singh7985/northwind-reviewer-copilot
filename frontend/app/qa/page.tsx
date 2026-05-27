"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function QAPage() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);

  async function loadHist() {
    setHistory(await api.qaHistory());
  }
  useEffect(() => {
    loadHist();
  }, []);

  async function ask() {
    if (q.trim().length < 2) return;
    setBusy(true);
    try {
      const a = await api.qa(q);
      setAnswer(a);
      await loadHist();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="bg-white border rounded-lg p-5">
        <h1 className="text-lg font-semibold">Policy Q&amp;A</h1>
        <p className="text-sm text-slate-500 mt-1">
          Ask anything about Northwind's policy library. Out-of-scope questions are refused
          rather than answered.
        </p>
        <div className="mt-3 flex gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            className="flex-1 border rounded p-2 text-sm"
            placeholder="e.g. Can I expense a beer on a solo trip?"
          />
          <button
            onClick={ask}
            disabled={busy}
            className="rounded bg-slate-900 text-white px-4 py-2 text-sm disabled:opacity-50"
          >
            {busy ? "…" : "Ask"}
          </button>
        </div>

        {answer && (
          <div
            className={`mt-4 rounded p-4 ${
              answer.refused
                ? "bg-amber-50 border border-amber-200"
                : "bg-emerald-50 border border-emerald-200"
            }`}
          >
            <div className="text-xs uppercase text-slate-500 mb-1">
              {answer.refused ? "Refused" : "Answer"}
            </div>
            <div className="text-sm whitespace-pre-wrap">{answer.answer}</div>
            {answer.citations?.length ? (
              <div className="mt-3 space-y-1">
                <div className="text-xs uppercase text-slate-500">Citations</div>
                {answer.citations.map((c: any, i: number) => (
                  <div key={i} className="bg-white border rounded p-2 text-sm">
                    <span className="text-xs text-slate-500">
                      {c.doc_id} §{c.section}
                    </span>{" "}
                    "{c.quote}"
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        )}
      </div>

      <div className="bg-white border rounded-lg p-5">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Recent Q&amp;A</h2>
        <ul className="space-y-2 text-sm">
          {history.map((h) => (
            <li key={h.id} className="border-b pb-2">
              <div className="text-slate-500 text-xs">
                {new Date(h.created_at).toLocaleString()} · {h.refused ? "refused" : "answered"}
              </div>
              <div className="font-medium">Q: {h.question}</div>
              <div className="text-slate-700">A: {h.answer.slice(0, 240)}{h.answer.length > 240 ? "…" : ""}</div>
            </li>
          ))}
          {!history.length && <li className="text-slate-500 text-sm">No questions yet.</li>}
        </ul>
      </div>
    </div>
  );
}

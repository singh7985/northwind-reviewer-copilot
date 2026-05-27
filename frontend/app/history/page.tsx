"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { VerdictBadge } from "@/components/VerdictBadge";

export default function HistoryPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [employees, setEmployees] = useState<any[]>([]);
  const [emp, setEmp] = useState("");
  const [status, setStatus] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  async function load() {
    const params = {
      employee_id: emp || undefined,
      status: status || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
    };
    const [s, e] = await Promise.all([api.submissions(params), api.employees()]);
    setRows(s);
    setEmployees(e);
  }
  useEffect(() => {
    load();
  }, [emp, status, dateFrom, dateTo]);

  const hasFilters = !!(emp || status || dateFrom || dateTo);
  function clearFilters() {
    setEmp("");
    setStatus("");
    setDateFrom("");
    setDateTo("");
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <h1 className="text-lg font-semibold mr-2">Submission history</h1>
        <label className="text-xs text-slate-600">
          Employee
          <select
            value={emp}
            onChange={(e) => setEmp(e.target.value)}
            className="block mt-1 border rounded p-1.5 text-sm"
          >
            <option value="">All employees</option>
            {employees.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-600">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="block mt-1 border rounded p-1.5 text-sm"
          >
            <option value="">All statuses</option>
            <option value="ready_to_approve">ready_to_approve</option>
            <option value="needs_review">needs_review</option>
            <option value="has_rejections">has_rejections</option>
            <option value="pending">pending</option>
          </select>
        </label>
        <label className="text-xs text-slate-600">
          From
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="block mt-1 border rounded p-1.5 text-sm"
          />
        </label>
        <label className="text-xs text-slate-600">
          To
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="block mt-1 border rounded p-1.5 text-sm"
          />
        </label>
        {hasFilters && (
          <button
            onClick={clearFilters}
            className="text-xs text-slate-500 underline"
          >
            Clear filters
          </button>
        )}
        <div className="ml-auto text-xs text-slate-500">
          {rows.length} submission{rows.length === 1 ? "" : "s"}
        </div>
      </div>

      <div className="bg-white border rounded-lg overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="text-left text-slate-500 border-b">
            <tr>
              <th className="px-3 py-2">Created</th>
              <th className="px-3 py-2">Employee</th>
              <th className="px-3 py-2">Purpose</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2 text-right">Claimed</th>
              <th className="px-3 py-2 text-right">Reimb.</th>
              <th className="px-3 py-2">Verdicts</th>
              <th className="px-3 py-2">Overrides</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const overrides = (r.receipts || []).flatMap(
                (rc: any) => rc.overrides || []
              );
              return (
                <tr key={r.id} className="border-b hover:bg-slate-50 align-top">
                  <td className="px-3 py-2 text-xs text-slate-500">
                    {new Date(r.created_at).toLocaleString()}
                  </td>
                  <td className="px-3 py-2">{r.employee_name}</td>
                  <td className="px-3 py-2 max-w-md truncate">{r.trip_purpose}</td>
                  <td className="px-3 py-2">{r.status}</td>
                  <td className="px-3 py-2 text-right">
                    ${r.total_claimed?.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 text-right text-emerald-700">
                    ${r.total_reimbursable?.toFixed(2)}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(r.counts || {})
                        .filter(([, v]) => (v as number) > 0)
                        .map(([k, v]) => (
                          <span key={k} className="text-xs">
                            <VerdictBadge verdict={k} /> ×{v as number}
                          </span>
                        ))}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {overrides.length === 0 ? (
                      <span className="text-xs text-slate-400">—</span>
                    ) : (
                      <details className="text-xs">
                        <summary className="cursor-pointer inline-flex items-center px-2 py-0.5 rounded-full bg-violet-100 text-violet-800 ring-1 ring-violet-300 font-medium">
                          {overrides.length} override
                          {overrides.length > 1 ? "s" : ""}
                        </summary>
                        <ul className="mt-2 space-y-1 max-w-xs">
                          {overrides.map((o: any) => (
                            <li key={o.id} className="text-slate-600">
                              <strong>{o.reviewer}</strong>:{" "}
                              <span className="font-mono">
                                {o.previous_verdict}
                              </span>{" "}
                              →{" "}
                              <span className="font-mono">{o.new_verdict}</span>
                              <div className="text-slate-500">
                                "{o.comment}"
                              </div>
                              <div className="text-slate-400">
                                {new Date(o.created_at).toLocaleString()}
                              </div>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <Link
                      href={`/submissions/${r.id}`}
                      className="text-blue-700 underline text-xs"
                    >
                      Open →
                    </Link>
                  </td>
                </tr>
              );
            })}
            {!rows.length && (
              <tr>
                <td
                  colSpan={9}
                  className="text-center py-6 text-slate-500 text-sm"
                >
                  No submissions match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

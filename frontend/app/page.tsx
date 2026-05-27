"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

type Employee = {
  id: string;
  name: string;
  grade: number;
  title: string;
  department: string;
  home_base: string;
};

export default function Home() {
  const router = useRouter();
  const [employees, setEmployees] = useState<Employee[] | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [tripPurpose, setTripPurpose] = useState("");
  const [tripStart, setTripStart] = useState("");
  const [tripEnd, setTripEnd] = useState("");
  const [empId, setEmpId] = useState("");
  const [creating, setCreating] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [demoBusy, setDemoBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const [h, emps] = await Promise.all([api.health(), api.employees()]);
      setHealth(h);
      setEmployees(emps);
      if (emps.length && !empId) setEmpId(emps[0].id);
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function doSeed() {
    setSeeding(true);
    try {
      await api.seed();
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSeeding(false);
    }
  }

  async function createAndGo() {
    if (!empId || !tripPurpose) return;
    setCreating(true);
    try {
      const sub = await api.createSubmission({
        employee_id: empId,
        trip_purpose: tripPurpose,
        trip_start: tripStart || null,
        trip_end: tripEnd || null,
      });
      router.push(`/submissions/${sub.id}`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  }

  async function runDemo(dir: string) {
    setDemoBusy(dir);
    try {
      const sub = await api.seedFromDisk(dir);
      router.push(`/submissions/${sub.id}`);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDemoBusy(null);
    }
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
      <section className="md:col-span-2 bg-white border rounded-lg p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Start a new submission</h1>
          <div className="text-xs text-slate-500">
            {health
              ? `API ok · LLM ${health.llm_enabled ? "enabled" : "disabled"}`
              : "API offline"}
          </div>
        </div>

        {!employees?.length && (
          <div className="rounded border border-amber-300 bg-amber-50 text-amber-800 px-3 py-2 text-sm flex items-center justify-between">
            <span>No employees seeded yet.</span>
            <button
              onClick={doSeed}
              disabled={seeding}
              className="px-3 py-1 rounded bg-amber-700 text-white text-xs"
            >
              {seeding ? "Seeding…" : "Seed employees + policies"}
            </button>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="text-sm">
            <span className="text-slate-600">Employee</span>
            <select
              value={empId}
              onChange={(e) => setEmpId(e.target.value)}
              className="mt-1 block w-full rounded border-slate-300 border p-2"
            >
              {employees?.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name} · Grade {e.grade} · {e.department}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="text-slate-600">Trip purpose</span>
            <input
              value={tripPurpose}
              onChange={(e) => setTripPurpose(e.target.value)}
              className="mt-1 block w-full rounded border-slate-300 border p-2"
              placeholder="e.g. Client review with Acme in Boston"
            />
          </label>
          <label className="text-sm">
            <span className="text-slate-600">Trip start</span>
            <input
              type="date"
              value={tripStart}
              onChange={(e) => setTripStart(e.target.value)}
              className="mt-1 block w-full rounded border-slate-300 border p-2"
            />
          </label>
          <label className="text-sm">
            <span className="text-slate-600">Trip end</span>
            <input
              type="date"
              value={tripEnd}
              onChange={(e) => setTripEnd(e.target.value)}
              className="mt-1 block w-full rounded border-slate-300 border p-2"
            />
          </label>
        </div>

        <button
          onClick={createAndGo}
          disabled={!empId || !tripPurpose || creating}
          className="rounded bg-slate-900 text-white px-4 py-2 text-sm disabled:opacity-50"
        >
          {creating ? "Creating…" : "Create submission →"}
        </button>

        {error && (
          <div className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded p-2">
            {error}
          </div>
        )}
      </section>

      <aside className="bg-white border rounded-lg p-6 space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">
          Demo: process a bundled submission
        </h2>
        <p className="text-xs text-slate-500">
          Runs the full pipeline (extract → rule engine → retrieve → adjudicate)
          on the case-study sample folders.
        </p>
        <div className="space-y-2">
          {[
            "01_clean_denver",
            "02_clean_boston_conf",
            "03_dinner_over_cap",
            "04_alcohol_solo_travel",
            "05_receipt_mismatch",
          ].map((d) => (
            <button
              key={d}
              onClick={() => runDemo(d)}
              disabled={!!demoBusy}
              className="w-full text-left rounded border px-3 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
            >
              {demoBusy === d ? "Processing…" : d}
            </button>
          ))}
        </div>
        <div className="pt-2 text-xs">
          <Link href="/history" className="text-blue-700 underline">
            View submission history →
          </Link>
        </div>
      </aside>

      <section className="md:col-span-3 bg-white border rounded-lg p-6">
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Employees</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="text-left text-slate-500 border-b">
              <tr>
                <th className="py-2 pr-4">ID</th>
                <th className="py-2 pr-4">Name</th>
                <th className="py-2 pr-4">Grade</th>
                <th className="py-2 pr-4">Title</th>
                <th className="py-2 pr-4">Department</th>
                <th className="py-2 pr-4">Home base</th>
              </tr>
            </thead>
            <tbody>
              {employees?.map((e) => (
                <tr key={e.id} className="border-b">
                  <td className="py-2 pr-4 font-mono text-xs">{e.id}</td>
                  <td className="py-2 pr-4">{e.name}</td>
                  <td className="py-2 pr-4">{e.grade}</td>
                  <td className="py-2 pr-4">{e.title}</td>
                  <td className="py-2 pr-4">{e.department}</td>
                  <td className="py-2 pr-4">{e.home_base}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

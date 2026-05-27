const API = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export const api = {
  health: () => req<any>("/health"),
  seed: () => req<any>("/admin/seed", { method: "POST" }),
  employees: () => req<any[]>("/employees"),
  createEmployee: (e: any) =>
    req<any>("/employees", { method: "POST", body: JSON.stringify(e) }),
  submissions: (params: Record<string, string | undefined> = {}) => {
    const clean: Record<string, string> = {};
    for (const [k, v] of Object.entries(params)) if (v) clean[k] = v;
    const q = new URLSearchParams(clean).toString();
    return req<any[]>(`/submissions${q ? `?${q}` : ""}`);
  },
  getSubmission: (id: string) => req<any>(`/submissions/${id}`),
  createSubmission: (body: any) =>
    req<any>("/submissions", { method: "POST", body: JSON.stringify(body) }),
  uploadReceipt: async (subId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`${API}/submissions/${subId}/receipts`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  seedFromDisk: (dir: string) =>
    req<any>(`/submissions/seed_from_disk/${dir}`, { method: "POST" }),
  override: (receiptId: string, body: any) =>
    req<any>(`/receipts/${receiptId}/override`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  qa: (question: string) =>
    req<any>("/qa", { method: "POST", body: JSON.stringify({ question }) }),
  qaHistory: () => req<any[]>("/qa/history"),
};

export const API_BASE = API;

type V = "compliant" | "flagged" | "rejected" | "needs_human_review" | string;

const STYLES: Record<string, string> = {
  compliant: "bg-emerald-100 text-emerald-800 ring-1 ring-emerald-300",
  flagged: "bg-amber-100 text-amber-800 ring-1 ring-amber-300",
  rejected: "bg-rose-100 text-rose-800 ring-1 ring-rose-300",
  needs_human_review: "bg-blue-100 text-blue-800 ring-1 ring-blue-300",
};

const LABEL: Record<string, string> = {
  compliant: "Compliant",
  flagged: "Flagged",
  rejected: "Rejected",
  needs_human_review: "Needs human review",
};

export function VerdictBadge({ verdict }: { verdict: V }) {
  const cls = STYLES[verdict] || "bg-slate-100 text-slate-700 ring-1 ring-slate-300";
  const label = LABEL[verdict] || verdict;
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${cls}`}
    >
      {label}
    </span>
  );
}

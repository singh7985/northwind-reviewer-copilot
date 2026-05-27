type Props = {
  /** 0..1 */
  value: number;
  /** Optional label prefix, defaults to "confidence" */
  label?: string;
};

/**
 * Color-coded confidence pill.
 *   >= 0.75 → emerald (high)
 *   >= 0.50 → amber  (medium)
 *   else    → rose   (low)
 *
 * The visual cue is intentional: the brief calls out trustworthiness as
 * the headline UX requirement, so confidence has to be glanceable.
 */
export function ConfidenceBadge({ value, label = "confidence" }: Props) {
  const pct = Math.round((value || 0) * 100);
  let cls = "bg-rose-100 text-rose-800 ring-1 ring-rose-300";
  let tone = "low";
  if (value >= 0.75) {
    cls = "bg-emerald-100 text-emerald-800 ring-1 ring-emerald-300";
    tone = "high";
  } else if (value >= 0.5) {
    cls = "bg-amber-100 text-amber-800 ring-1 ring-amber-300";
    tone = "medium";
  }
  return (
    <span
      title={`${tone} ${label}`}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cls}`}
    >
      <span className="opacity-70">{label}</span>
      <strong>{pct}%</strong>
    </span>
  );
}

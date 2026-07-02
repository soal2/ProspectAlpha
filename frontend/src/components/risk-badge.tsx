import { cn } from "@/lib/utils";

const styles: Record<string, string> = {
  high: "bg-[color:var(--risk-high)]/12 text-[color:var(--risk-high)] ring-[color:var(--risk-high)]/30",
  mid: "bg-[color:var(--risk-mid)]/15 text-[oklch(0.45_0.12_75)] ring-[color:var(--risk-mid)]/40",
  low: "bg-[color:var(--risk-low)]/12 text-[color:var(--risk-low)] ring-[color:var(--risk-low)]/30",
};
const labels: Record<string, string> = { high: "高风险", mid: "中风险", low: "低风险" };

export function RiskBadge({
  level,
  className,
  withDot = true,
}: {
  level: "high" | "mid" | "low";
  className?: string;
  withDot?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        styles[level],
        className,
      )}
    >
      {withDot && <span className="size-1.5 rounded-full bg-current" />}
      {labels[level]}
    </span>
  );
}

export function RiskDot({
  level,
  className,
}: {
  level: "high" | "mid" | "low";
  className?: string;
}) {
  const colors: Record<string, string> = {
    high: "bg-[color:var(--risk-high)]",
    mid: "bg-[color:var(--risk-mid)]",
    low: "bg-[color:var(--risk-low)]",
  };
  return <span className={cn("inline-block size-2 rounded-full", colors[level], className)} />;
}

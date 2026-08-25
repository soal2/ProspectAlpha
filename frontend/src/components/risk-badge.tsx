import { cn } from "@/lib/utils";
import { type RiskLevel } from "@/lib/types";

const styles: Record<RiskLevel, string> = {
  very_high:
    "bg-[color:var(--risk-very-high)]/15 text-[color:var(--risk-very-high)] ring-[color:var(--risk-very-high)]/30",
  high: "bg-[color:var(--risk-high)]/12 text-[color:var(--risk-high)] ring-[color:var(--risk-high)]/30",
  medium:
    "bg-[color:var(--risk-medium)]/15 text-[oklch(0.45_0.12_75)] ring-[color:var(--risk-medium)]/40",
  low: "bg-[color:var(--risk-low)]/12 text-[color:var(--risk-low)] ring-[color:var(--risk-low)]/30",
  very_low:
    "bg-[color:var(--risk-very-low)]/12 text-[color:var(--risk-very-low)] ring-[color:var(--risk-very-low)]/30",
  unrated: "bg-muted text-muted-foreground ring-border",
};
const labels: Record<RiskLevel, string> = {
  very_high: "极高风险",
  high: "高风险",
  medium: "中风险",
  low: "低风险",
  very_low: "极低风险",
  unrated: "未评级",
};

export function RiskBadge({
  level,
  className,
  withDot = true,
}: {
  level: RiskLevel;
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
  level: RiskLevel;
  className?: string;
}) {
  const colors: Record<RiskLevel, string> = {
    very_high: "bg-[color:var(--risk-very-high)]",
    high: "bg-[color:var(--risk-high)]",
    medium: "bg-[color:var(--risk-medium)]",
    low: "bg-[color:var(--risk-low)]",
    very_low: "bg-[color:var(--risk-very-low)]",
    unrated: "bg-muted-foreground",
  };
  return <span className={cn("inline-block size-2 rounded-full", colors[level], className)} />;
}

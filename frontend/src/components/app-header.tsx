import { SidebarTrigger } from "@/components/ui/sidebar";
import { Link, useRouterState } from "@tanstack/react-router";
import { ChevronRight, Search, Bell, HelpCircle } from "lucide-react";

const labels: Record<string, string> = {
  "": "工作台",
  intake: "招股书接入",
  analyses: "我的分析",
  evidence: "证据知识库",
  experts: "Agent 专家公会",
  "risk-rules": "风险体系配置",
  backtest: "回测中心",
  monitor: "风险监控中心",
  report: "IPO 风险报告",
  trace: "决策链路回溯",
  new: "新建回测",
};

export function AppHeader() {
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const segs = pathname.split("/").filter(Boolean);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-white/85 px-4 backdrop-blur supports-[backdrop-filter]:bg-white/70">
      <SidebarTrigger className="-ml-1 text-muted-foreground hover:text-foreground" />
      <div className="mx-1 hidden h-5 w-px bg-border md:block" />
      <nav className="flex min-w-0 items-center gap-1.5 text-[13px]">
        <Link to="/" className="text-muted-foreground hover:text-foreground">首页</Link>
        {segs.map((seg, i) => {
          const path = "/" + segs.slice(0, i + 1).join("/");
          const isLast = i === segs.length - 1;
          const label = labels[seg] ?? seg;
          return (
            <span key={path} className="flex items-center gap-1.5">
              <ChevronRight className="size-3 text-muted-foreground/50" />
              {isLast ? (
                <span className="truncate font-medium text-foreground">{label}</span>
              ) : (
                <a href={path} className="truncate text-muted-foreground hover:text-foreground">{label}</a>
              )}
            </span>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        <div className="hidden items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-xs text-muted-foreground md:flex">
          <Search className="size-3.5" />
          <span>搜索公司 / 证据 / 风险条款</span>
          <kbd className="ml-3 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] tabular text-muted-foreground">⌘K</kbd>
        </div>
        <span className="hidden items-center gap-1.5 rounded-sm border border-[color:var(--gold)]/50 bg-[color:var(--gold)]/8 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-[color:var(--gold)] md:inline-flex">
          <span className="size-1 rounded-full bg-[color:var(--gold)]" />
          Institutional
        </span>
        <button className="grid size-9 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground">
          <Bell className="size-4" />
        </button>
        <button className="grid size-9 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground">
          <HelpCircle className="size-4" />
        </button>
      </div>
    </header>
  );
}

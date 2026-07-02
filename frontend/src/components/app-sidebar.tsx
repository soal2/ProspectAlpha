import { Link, useRouterState } from "@tanstack/react-router";
import {
  Home,
  FileSearch,
  ListChecks,
  FileBarChart,
  Database,
  Users,
  Settings2,
  LineChart,
  Activity,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";

const work = [
  { title: "工作台", url: "/", icon: Home },
  { title: "招股书接入", url: "/intake", icon: FileSearch },
  { title: "我的分析", url: "/analyses", icon: ListChecks },
];

const intel = [
  { title: "证据知识库", url: "/evidence", icon: Database },
  { title: "Agent 专家公会", url: "/experts", icon: Users },
  { title: "风险体系配置", url: "/risk-rules", icon: Settings2 },
];

const ops = [
  { title: "回测中心", url: "/backtest", icon: LineChart },
  { title: "风险监控中心", url: "/monitor", icon: Activity },
];

export function AppSidebar() {
  const { state } = useSidebar();
  const collapsed = state === "collapsed";
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const isActive = (url: string) => (url === "/" ? pathname === "/" : pathname.startsWith(url));

  const renderItem = (item: { title: string; url: string; icon: typeof Home }) => {
    const active = isActive(item.url);
    return (
      <SidebarMenuItem key={item.url}>
        <SidebarMenuButton
          asChild
          isActive={active}
          tooltip={item.title}
          className="relative rounded-none h-9 text-[13px] text-sidebar-foreground/75 hover:bg-white/[0.04] hover:text-sidebar-foreground data-[active=true]:bg-[color:var(--sidebar-accent)] data-[active=true]:text-[color:var(--sidebar-accent-foreground)] data-[active=true]:font-medium"
        >
          <Link to={item.url} className="flex items-center gap-3">
            {active && (
              <span className="pointer-events-none absolute left-0 top-1 bottom-1 w-[3px] rounded-r-sm bg-[color:var(--gold)]" />
            )}
            <item.icon
              className={`size-4 shrink-0 ${active ? "text-[color:var(--gold)]" : "text-sidebar-foreground/55"}`}
            />
            <span className="truncate">{item.title}</span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    );
  };

  return (
    <Sidebar collapsible="icon" className="[--sidebar-width:15.5rem]">
      <SidebarHeader className="border-b border-sidebar-border bg-[color:var(--navy-deep)]">
        <Link to="/" className="flex items-center gap-3 px-2 py-3">
          <div className="relative grid size-9 shrink-0 place-items-center rounded-sm bg-[color:var(--navy)] ring-1 ring-[color:var(--gold)]/40">
            {/* Gold geometric mark: rotated square + diagonal stroke */}
            <div className="relative size-4">
              <span className="absolute inset-0 rotate-45 border border-[color:var(--gold)]" />
              <span className="absolute left-1/2 top-1/2 h-[7px] w-[7px] -translate-x-1/2 -translate-y-1/2 bg-[color:var(--gold)]" />
            </div>
          </div>
          {!collapsed && (
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="brand-mark truncate text-[17px] text-sidebar-foreground">
                ProspectAlpha
              </span>
              <span className="mt-0.5 truncate text-[9.5px] font-medium uppercase tracking-[0.22em] text-[color:var(--gold)]/75">
                IPO Risk Intelligence
              </span>
            </div>
          )}
        </Link>
      </SidebarHeader>

      <SidebarContent className="bg-[color:var(--sidebar)]">
        <div className="mx-3 my-2 h-px gold-rule" />

        <SidebarGroup>
          <SidebarGroupLabel className="text-[10px] font-semibold uppercase tracking-[0.18em] text-sidebar-foreground/40">
            Workflow · 工作流
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{work.map(renderItem)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel className="text-[10px] font-semibold uppercase tracking-[0.18em] text-sidebar-foreground/40">
            Intelligence · 知识与专家
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{intel.map(renderItem)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel className="text-[10px] font-semibold uppercase tracking-[0.18em] text-sidebar-foreground/40">
            Evaluation · 评估与监控
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{ops.map(renderItem)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {!collapsed && (
          <div className="mx-3 mt-4 border border-[color:var(--gold)]/20 bg-white/[0.02] p-3">
            <div className="flex items-center justify-between">
              <span className="eyebrow text-[color:var(--gold)]/80">Workspace</span>
              <span className="inline-flex size-1.5 rounded-full bg-[color:var(--gold)]" />
            </div>
            <div className="mt-2 flex items-baseline justify-between">
              <span className="text-xs text-sidebar-foreground/60">Analyses</span>
              <span className="tabular text-sm text-sidebar-foreground">25</span>
            </div>
            <div className="mt-1 flex items-baseline justify-between">
              <span className="text-xs text-sidebar-foreground/60">Evidence</span>
              <span className="tabular text-sm text-sidebar-foreground">4,218</span>
            </div>
            <div className="mt-3 h-px bg-white/10" />
            <div className="mt-2 flex items-center justify-between text-[10px] text-sidebar-foreground/50">
              <span>SESSION</span>
              <span className="tabular">14:24 HKT</span>
            </div>
          </div>
        )}
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border bg-[color:var(--navy-deep)]">
        <div className="flex items-center gap-2 px-2 py-2">
          <div className="relative grid size-8 shrink-0 place-items-center rounded-sm bg-[color:var(--gold)] text-[11px] font-semibold text-[color:var(--navy-deep)]">
            研
            <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full border border-[color:var(--navy-deep)] bg-emerald-400" />
          </div>
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium leading-tight text-sidebar-foreground">
                林研究员
              </div>
              <div className="mt-0.5 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-sidebar-foreground/50">
                <span>Institutional</span>
                <span className="text-[color:var(--gold)]/70">·</span>
                <span>HK IPO Desk</span>
              </div>
            </div>
          )}
        </div>
      </SidebarFooter>
    </Sidebar>
  );
}

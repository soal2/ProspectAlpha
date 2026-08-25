import { createFileRoute, Link } from "@tanstack/react-router";
import { Plus, Search, Network, Trash2 } from "lucide-react";
import { useState, useEffect, useMemo } from "react";
import { toast } from "sonner";
import { deleteAnalysisRun, getAnalyses } from "@/lib/data-service";
import { type AnalysisSummary } from "@/lib/types";
import { RiskBadge } from "@/components/risk-badge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

export const Route = createFileRoute("/analyses/")({
  head: () => ({
    meta: [
      { title: "我的分析 · ProspectAlpha IPO RiskAgent" },
      {
        name: "description",
        content: "全部已完成的港股IPO风险穿透预警报告，结论可溯源，证据可沉淀。",
      },
    ],
  }),
  component: AnalysesIndex,
});

const filters = ["全部", "极高风险", "高风险", "中风险", "低风险", "极低风险", "未评级", "专家级", "近 7 天"] as const;
type AnalysisFilter = (typeof filters)[number];
type AnalysisSort = "newest" | "oldest" | "evidence";

function AnalysesIndex() {
  const [analyses, setAnalyses] = useState<AnalysisSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");
  const [activeFilter, setActiveFilter] = useState<AnalysisFilter>("全部");
  const [sortBy, setSortBy] = useState<AnalysisSort>("newest");
  const [deleteCandidate, setDeleteCandidate] = useState<AnalysisSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAnalyses().then((data) => {
      if (!cancelled) {
        setAnalyses(data);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const visibleAnalyses = useMemo(() => {
    const query = keyword.trim().toLowerCase();
    const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const selected = analyses.filter((analysis) => {
      if (
        query &&
        !`${analysis.name} ${analysis.code} ${analysis.industry}`.toLowerCase().includes(query)
      ) {
        return false;
      }
      if (activeFilter === "极高风险" && analysis.riskLevel !== "very_high") return false;
      if (activeFilter === "高风险" && analysis.riskLevel !== "high") return false;
      if (activeFilter === "中风险" && analysis.riskLevel !== "medium") return false;
      if (activeFilter === "低风险" && analysis.riskLevel !== "low") return false;
      if (activeFilter === "极低风险" && analysis.riskLevel !== "very_low") return false;
      if (activeFilter === "未评级" && analysis.riskLevel !== "unrated") return false;
      if (activeFilter === "专家级" && analysis.mode !== "expert") return false;
      if (activeFilter === "近 7 天" && Date.parse(analysis.createdAt) < sevenDaysAgo) return false;
      return true;
    });
    return selected.sort((a, b) => {
      if (sortBy === "evidence") return b.evidenceCount - a.evidenceCount;
      const delta = Date.parse(b.createdAt) - Date.parse(a.createdAt);
      return sortBy === "oldest" ? -delta : delta;
    });
  }, [activeFilter, analyses, keyword, sortBy]);

  async function confirmDelete() {
    if (!deleteCandidate) return;
    setDeleting(true);
    try {
      await deleteAnalysisRun(deleteCandidate.id);
      setAnalyses((current) => current.filter((item) => item.id !== deleteCandidate.id));
      toast.success(`已删除 ${deleteCandidate.name} 的分析报告`);
      setDeleteCandidate(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除分析报告失败");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="px-6 py-8 sm:px-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold">我的分析</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            所有已完成的港股IPO风险穿透预警报告 · 结论可溯源、证据可沉淀
          </p>
        </div>
        <Link
          to="/"
          className="inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition hover:bg-primary/90"
        >
          <Plus className="size-4" /> 发起新分析
        </Link>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜索公司、申请编号、拟上市代码或行业"
            className="h-9 w-72 rounded-full border border-border bg-card pl-9 pr-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
        {filters.map((filter) => (
          <button
            key={filter}
            type="button"
            onClick={() => setActiveFilter(filter)}
            className={`inline-flex h-8 items-center rounded-full px-3 text-xs font-medium ${activeFilter === filter ? "bg-primary text-primary-foreground" : "border border-border bg-card text-muted-foreground hover:bg-accent"}`}
          >
            {filter}
          </button>
        ))}
        <select
          value={sortBy}
          onChange={(event) => setSortBy(event.target.value as AnalysisSort)}
          aria-label="报告排序"
          className="ml-auto h-8 rounded-full border border-border bg-card px-3 text-xs text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <option value="newest">最新生成</option>
          <option value="oldest">最早生成</option>
          <option value="evidence">证据最多</option>
        </select>
      </div>

      {analyses.length > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          显示 {visibleAnalyses.length}/{analyses.length} 条分析
        </p>
      )}

      <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {visibleAnalyses.map((a) => (
          <article
            key={a.id}
            className="group flex flex-col overflow-hidden rounded-2xl border border-border bg-card transition hover:border-primary/30 hover:shadow-sm"
          >
            <div className="relative h-32 bg-gradient-to-br from-[color:var(--sage-soft)] via-[color:var(--amber-soft)]/40 to-[color:var(--paper)]">
              <div className="absolute inset-0 grid-paper opacity-60" />
              <div className="absolute left-4 top-4">
                <RiskBadge level={a.riskLevel} />
              </div>
              <div className="absolute right-4 top-4 rounded-md bg-background/80 px-2 py-1 text-[11px] tabular text-muted-foreground backdrop-blur">
                {a.code}
              </div>
              <div className="absolute bottom-3 right-4 text-right">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                  风险评级
                </div>
                <div className="font-serif text-xl font-semibold text-foreground/80">
                  {riskRatingLabel(a.riskLevel)}
                </div>
              </div>
            </div>
            <div className="flex flex-1 flex-col p-4">
              <h3 className="font-medium leading-snug">{a.name} · 港股IPO风险穿透预警报告</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {a.industry} · 上市 {a.listingDate}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                基于 {a.evidenceCount} 条证据 · {a.experts} 位专家协作生成 ·{" "}
                {a.mode === "expert" ? "专家级模式" : a.mode === "deep" ? "深度模式" : "快速模式"}
              </p>
              <div className="mt-4 flex items-center justify-between border-t border-border pt-3 text-xs text-muted-foreground">
                <div className="flex items-center gap-3 tabular">
                  <span>{a.evidenceCount} 证据</span>
                  <span>·</span>
                  <span>{a.highConfidence} 高置信</span>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setDeleteCandidate(a)}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-destructive hover:bg-destructive/8"
                    aria-label={`删除 ${a.name} 的分析报告`}
                  >
                    <Trash2 className="size-3" /> 删除
                  </button>
                  <Link
                    to="/analyses/$id/trace"
                    params={{ id: a.id }}
                    className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-primary hover:bg-primary/8"
                  >
                    <Network className="size-3" /> 链路
                  </Link>
                  <Link
                    to="/analyses/$id/report"
                    params={{ id: a.id }}
                    className="inline-flex items-center rounded-full bg-primary/8 px-2.5 py-1 text-primary hover:bg-primary/12"
                  >
                    报告 →
                  </Link>
                </div>
              </div>
            </div>
          </article>
        ))}
      </div>
      {!loading && analyses.length > 0 && visibleAnalyses.length === 0 ? (
        <div className="mt-6 rounded-2xl border border-dashed border-border bg-card px-6 py-10 text-center text-sm text-muted-foreground">
          没有符合当前筛选条件的分析报告。
        </div>
      ) : null}
      {!loading && analyses.length === 0 ? (
        <div className="mt-6 rounded-2xl border border-dashed border-border bg-card px-6 py-10 text-center">
          <h2 className="font-serif text-xl font-semibold">当前数据库中还没有分析任务</h2>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground">
            招股书已经入库并不等于已生成分析报告。请回到工作台选择已有
            PDF，再点击“启动分析”；创建成功后会出现在这里。
          </p>
          <Link
            to="/"
            className="mt-5 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            <Plus className="size-4" /> 前往工作台选择已有 PDF
          </Link>
        </div>
      ) : null}
      <AlertDialog
        open={Boolean(deleteCandidate)}
        onOpenChange={(open) => {
          if (!open && !deleting) setDeleteCandidate(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除分析报告？</AlertDialogTitle>
            <AlertDialogDescription>
              {`将删除“${deleteCandidate?.name ?? ""}”的分析记录、报告和本次沉淀的证据；原始招股书文件会保留。此操作不可撤销。`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting}
              onClick={(event) => {
                event.preventDefault();
                void confirmDelete();
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? "删除中…" : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function riskRatingLabel(level: AnalysisSummary["riskLevel"]) {
  return {
    very_high: "极高风险",
    high: "高风险",
    medium: "中风险",
    low: "低风险",
    very_low: "极低风险",
    unrated: "未评级",
  }[level];
}

import { createFileRoute } from "@tanstack/react-router";
import {
  CheckCircle2,
  Database,
  Edit3,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  createRiskRule,
  deleteRiskRule,
  getRiskRulesData,
  updateRiskRule,
  type RiskRulePayload,
} from "@/lib/data-service";
import type { RiskRule, RiskRulesData } from "@/mocks";

export const Route = createFileRoute("/risk-rules")({
  head: () => ({
    meta: [
      { title: "风险体系配置 · ProspectAlpha IPO RiskAgent" },
      { name: "description", content: "金融专家定义风险分类、阈值、权重 · 技术团队负责工程化抽取" },
    ],
  }),
  component: RiskRulesPage,
});

const dimensionColors: Record<string, string> = {
  D1: "var(--chart-1)",
  D2: "var(--chart-4)",
  D3: "var(--chart-3)",
};

const emptyRule = (data: RiskRulesData | null, dimensionCode: string): RiskRulePayload => {
  const dimension = data?.dimensions.find((item) => item.dimension_code === dimensionCode);
  return {
    dimension_code: dimension?.dimension_code ?? "D1",
    dimension: dimension?.dimension ?? "维度1：标准化财务指标风险",
    rule_type: dimension?.rule_type ?? "quantitative_financial",
    category: dimension?.categories[0]?.name ?? "",
    risk_item: "",
    description: "",
    trigger_condition: "",
    calculation_or_method: "",
    source_sections: "",
    enabled: true,
  };
};

function RiskRulesPage() {
  const [data, setData] = useState<RiskRulesData | null>(null);
  const [activeDimension, setActiveDimension] = useState("D1");
  const [activeCategory, setActiveCategory] = useState("全部");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<RiskRulePayload>(emptyRule(null, "D1"));
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await getRiskRulesData();
      setData(next);
      const dimensionCode = next.dimensions[0]?.dimension_code ?? "D1";
      setActiveDimension((current) =>
        next.dimensions.some((item) => item.dimension_code === current) ? current : dimensionCode,
      );
      const firstRule = next.rules[0];
      if (firstRule) {
        setSelectedId(firstRule.rule_id);
        setDraft(toPayload(firstRule));
        setCreating(false);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "风险规则加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const activeDimensionMeta = data?.dimensions.find(
    (item) => item.dimension_code === activeDimension,
  );

  const categories = useMemo(() => {
    const names = activeDimensionMeta?.categories.map((item) => item.name) ?? [];
    return ["全部", ...names];
  }, [activeDimensionMeta]);

  const filteredRules = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return (data?.rules ?? []).filter((rule) => {
      const inDimension = rule.dimension_code === activeDimension;
      const inCategory = activeCategory === "全部" || rule.category === activeCategory;
      const haystack = [
        rule.rule_id,
        rule.category,
        rule.risk_item,
        rule.description,
        rule.trigger_condition,
        rule.source_sections,
      ]
        .join(" ")
        .toLowerCase();
      return inDimension && inCategory && (!keyword || haystack.includes(keyword));
    });
  }, [activeCategory, activeDimension, data, query]);

  const selectRule = (rule: RiskRule) => {
    setSelectedId(rule.rule_id);
    setDraft(toPayload(rule));
    setCreating(false);
    setError(null);
  };

  const startCreate = () => {
    setSelectedId(null);
    setDraft(emptyRule(data, activeDimension));
    setCreating(true);
    setError(null);
  };

  const saveRule = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved =
        creating || !selectedId
          ? await createRiskRule(draft)
          : await updateRiskRule(selectedId, draft);
      const next = await getRiskRulesData();
      setData(next);
      setSelectedId(saved.rule_id);
      setDraft(toPayload(saved));
      setCreating(false);
      setActiveDimension(saved.dimension_code);
      setActiveCategory(saved.category);
    } catch (err) {
      setError(err instanceof Error ? err.message : "风险规则保存失败");
    } finally {
      setSaving(false);
    }
  };

  const removeRule = async () => {
    if (!selectedId || !window.confirm(`删除规则 ${selectedId}？`)) return;
    setSaving(true);
    setError(null);
    try {
      await deleteRiskRule(selectedId);
      const next = await getRiskRulesData();
      setData(next);
      const nextRule =
        next.rules.find((rule) => rule.dimension_code === activeDimension) ?? next.rules[0];
      setSelectedId(nextRule?.rule_id ?? null);
      setDraft(nextRule ? toPayload(nextRule) : emptyRule(next, activeDimension));
      setCreating(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "风险规则删除失败");
    } finally {
      setSaving(false);
    }
  };

  const updateDraft = (patch: Partial<RiskRulePayload>) => {
    setDraft((current) => ({ ...current, ...patch }));
  };

  const setDraftDimension = (dimensionCode: string) => {
    const dimension = data?.dimensions.find((item) => item.dimension_code === dimensionCode);
    if (!dimension) return;
    setDraft((current) => ({
      ...current,
      dimension_code: dimension.dimension_code,
      dimension: dimension.dimension,
      rule_type: dimension.rule_type,
      category: dimension.categories[0]?.name ?? current.category,
    }));
  };

  if (loading && !data) {
    return (
      <div className="flex h-[calc(100vh-3.5rem)] items-center justify-center text-sm text-muted-foreground">
        加载中...
      </div>
    );
  }

  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-[260px_minmax(0,1fr)_420px] overflow-hidden">
      <aside className="border-r border-border bg-card/70 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-serif text-lg font-semibold">风险体系</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {data?.totals.dimension_count ?? 0} 维度 · {data?.totals.rule_count ?? 0} 规则
            </p>
          </div>
          <button
            onClick={() => void load()}
            className="inline-flex size-8 items-center justify-center rounded-md border border-border bg-background text-muted-foreground hover:text-foreground"
            title="刷新"
          >
            <RefreshCw className="size-4" />
          </button>
        </div>

        <div className="mt-5 space-y-1">
          {(data?.dimensions ?? []).map((dimension) => (
            <button
              key={dimension.dimension_code}
              onClick={() => {
                setActiveDimension(dimension.dimension_code);
                setActiveCategory("全部");
              }}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${
                activeDimension === dimension.dimension_code
                  ? "bg-primary/8 text-primary"
                  : "text-muted-foreground hover:bg-accent"
              }`}
            >
              <span className="flex items-center gap-2">
                <span
                  className="size-2 rounded-full"
                  style={{
                    background: dimensionColors[dimension.dimension_code] ?? "var(--chart-6)",
                  }}
                />
                <span className="truncate">{dimension.dimension}</span>
              </span>
              <span className="tabular text-[11px]">{dimension.rule_count}</span>
            </button>
          ))}
        </div>

        <div className="mt-6 rounded-lg border border-border bg-background p-3">
          {/* <div className="flex items-center gap-2 text-xs font-medium">
            <Database className="size-3.5 text-primary" />
            {data?.version ?? "v1.0"}
          </div> */}
          <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[11px]">
            <Metric label="维度" value={data?.totals.dimension_count ?? 0} />
            <Metric label="分类" value={data?.totals.category_count ?? 0} />
            <Metric label="规则" value={data?.totals.rule_count ?? 0} />
          </div>
        </div>

        <div className="mt-5 space-y-1">
          {categories.map((category) => (
            <button
              key={category}
              onClick={() => setActiveCategory(category)}
              className={`flex w-full items-center justify-between rounded-md px-3 py-1.5 text-xs ${
                activeCategory === category
                  ? "bg-secondary text-secondary-foreground"
                  : "text-muted-foreground hover:bg-muted"
              }`}
            >
              <span className="truncate">{category}</span>
              <span className="tabular">
                {category === "全部"
                  ? activeDimensionMeta?.rule_count
                  : activeDimensionMeta?.categories.find((item) => item.name === category)
                      ?.rule_count}
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="flex min-w-0 flex-col overflow-hidden">
        <div className="border-b border-border bg-background/70 px-6 py-4">
          <div className="flex items-end justify-between gap-4">
            <div className="min-w-0">
              <h1 className="truncate font-serif text-2xl font-semibold">
                {activeDimensionMeta?.dimension ?? "风险规则"}
              </h1>
              <p className="mt-1 text-xs text-muted-foreground">
                {filteredRules.length} 条当前结果
              </p>
            </div>
            <button
              onClick={startCreate}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90"
            >
              <Plus className="size-4" />
              新增
            </button>
          </div>
          <div className="mt-4 flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2">
            <Search className="size-4 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索规则编号、风险项、阈值或章节"
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {error ? (
            <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/8 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          ) : null}

          <div className="space-y-2">
            {filteredRules.map((rule) => (
              <button
                key={rule.rule_id}
                onClick={() => selectRule(rule)}
                className={`w-full rounded-lg border bg-card p-4 text-left transition hover:border-primary/40 ${
                  selectedId === rule.rule_id ? "border-primary shadow-sm" : "border-border"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="tabular text-[11px] text-muted-foreground">
                        {rule.rule_id}
                      </span>
                      <span className="rounded-md bg-secondary px-2 py-0.5 text-[11px] text-secondary-foreground">
                        {rule.category}
                      </span>
                      {rule.enabled ? (
                        <CheckCircle2 className="size-3.5 text-[color:var(--risk-low)]" />
                      ) : (
                        <XCircle className="size-3.5 text-muted-foreground" />
                      )}
                    </div>
                    <div className="mt-2 font-medium leading-snug">{rule.risk_item}</div>
                    <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                      {rule.trigger_condition}
                    </div>
                  </div>
                  <Edit3 className="mt-1 size-4 shrink-0 text-muted-foreground" />
                </div>
              </button>
            ))}
          </div>

          {filteredRules.length === 0 ? (
            <div className="py-16 text-center text-xs text-muted-foreground">暂无匹配规则</div>
          ) : null}
        </div>
      </section>

      <aside className="min-w-0 overflow-y-auto border-l border-border bg-card/70 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="font-serif text-lg font-semibold">
              {creating ? "新增规则" : "规则编辑"}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">{selectedId ?? "未分配编号"}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void saveRule()}
              disabled={saving || !draft.risk_item.trim()}
              className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Save className="size-4" />
              保存
            </button>
            <button
              onClick={() => void removeRule()}
              disabled={saving || creating || !selectedId}
              className="inline-flex size-8 items-center justify-center rounded-md border border-border text-destructive hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-40"
              title="删除"
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        </div>

        <div className="mt-5 space-y-4">
          <Field label="风险项">
            <input
              value={draft.risk_item}
              onChange={(event) => updateDraft({ risk_item: event.target.value })}
              className="field-input"
            />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="维度">
              <select
                value={draft.dimension_code}
                onChange={(event) => setDraftDimension(event.target.value)}
                className="field-input"
              >
                {(data?.dimensions ?? []).map((dimension) => (
                  <option key={dimension.dimension_code} value={dimension.dimension_code}>
                    {dimension.dimension_code}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="规则类型">
              <input value={draft.rule_type} readOnly className="field-input bg-muted" />
            </Field>
          </div>

          <Field label="一级分类">
            <input
              value={draft.category}
              onChange={(event) => updateDraft({ category: event.target.value })}
              className="field-input"
              list="risk-rule-categories"
            />
            <datalist id="risk-rule-categories">
              {(activeDimensionMeta?.categories ?? []).map((category) => (
                <option key={category.name} value={category.name} />
              ))}
            </datalist>
          </Field>

          <Field label="风险描述">
            <textarea
              value={draft.description}
              onChange={(event) => updateDraft({ description: event.target.value })}
              rows={4}
              className="field-input resize-none"
            />
          </Field>

          <Field label="阈值 / 预警条件">
            <textarea
              value={draft.trigger_condition}
              onChange={(event) => updateDraft({ trigger_condition: event.target.value })}
              rows={3}
              className="field-input resize-none"
            />
          </Field>

          <Field label="计算方式 / 公式">
            <textarea
              value={draft.calculation_or_method}
              onChange={(event) => updateDraft({ calculation_or_method: event.target.value })}
              rows={3}
              className="field-input resize-none"
            />
          </Field>

          <Field label="数据来源章节">
            <input
              value={draft.source_sections}
              onChange={(event) => updateDraft({ source_sections: event.target.value })}
              className="field-input"
            />
          </Field>

          <label className="flex items-center justify-between rounded-md border border-border bg-background px-3 py-2 text-sm">
            <span>启用</span>
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => updateDraft({ enabled: event.target.checked })}
              className="size-4 accent-[color:var(--primary)]"
            />
          </label>
        </div>
      </aside>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md bg-muted px-2 py-2">
      <div className="tabular text-sm font-semibold">{value}</div>
      <div className="mt-0.5 text-muted-foreground">{label}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-xs font-medium text-muted-foreground">
      <span>{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

function toPayload(rule: RiskRule): RiskRulePayload {
  return {
    dimension_code: rule.dimension_code,
    dimension: rule.dimension,
    rule_type: rule.rule_type,
    category: rule.category,
    risk_item: rule.risk_item,
    description: rule.description,
    trigger_condition: rule.trigger_condition,
    calculation_or_method: rule.calculation_or_method,
    source_sections: rule.source_sections,
    enabled: rule.enabled,
  };
}

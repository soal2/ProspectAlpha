import { createFileRoute, Link } from "@tanstack/react-router";
import { ArrowLeft, CircleAlert } from "lucide-react";

export const Route = createFileRoute("/backtest/new")({
  head: () => ({ meta: [{ title: "新建回测 · ProspectAlpha" }] }),
  component: NewBacktest,
});

function NewBacktest() {
  return (
    <div className="px-6 py-8 sm:px-10">
      <Link
        to="/backtest"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> 返回回测中心
      </Link>
      <div className="mt-8 max-w-2xl rounded-2xl border border-border bg-card p-6">
        <CircleAlert className="size-5 text-muted-foreground" />
        <h1 className="mt-3 font-serif text-2xl font-semibold">回测服务暂未接入</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          当前后端未提供回测任务创建与结果查询接口。为避免展示虚构的模型、样本和指标，本页面不会生成模拟配置或结果。
        </p>
      </div>
    </div>
  );
}

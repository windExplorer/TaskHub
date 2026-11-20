import { useCallback, useEffect, useState } from "react";

import { ApiFailure, fetchStatsDetail } from "../api";
import { CHART, STATUS_COLOR, STATUS_META, TYPE_COLOR, TYPE_META } from "../constants";
import { Select } from "./ui/Select";
import { fmt, fmtAxis } from "../lib/ui";
import type { StatsDetail } from "../types";
import { Card } from "./Card";
import { StatCard } from "./StatCard";
import { AnimatedNumber } from "./AnimatedNumber";
import { LineChart } from "./charts/LineChart";
import { DonutChart } from "./charts/DonutChart";

const RANGES = [
  { v: 24, label: "近 24 小时" },
  { v: 168, label: "近 7 天" },
  { v: 720, label: "近 30 天" },
];

/** 任务统计（原 stats.html）：时间范围 + 指标卡 + 趋势/分布图。 */
export function StatsPage() {
  const [hours, setHours] = useState(24);
  const [data, setData] = useState<StatsDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (h: number, alive: () => boolean) => {
    try {
      const d = await fetchStatsDetail(h);
      if (!alive()) return;
      setData(d);
      setErr(null);
    } catch (e) {
      if (!alive()) return;
      setErr(e instanceof ApiFailure ? e.detail : String(e));
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    void load(hours, () => mounted);
    return () => {
      mounted = false;
    };
  }, [hours, load]);

  const s = data?.summary;
  const labels = (data?.series ?? []).map((p) => fmtAxis(p.ts, data?.bucket ?? "hour"));

  const statusItems = Object.entries(data?.by_status ?? {})
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({
      label: STATUS_META[k as keyof typeof STATUS_META]?.label ?? k,
      value: v,
      color: STATUS_COLOR[k as keyof typeof STATUS_COLOR] ?? "var(--mute)",
    }));
  const typeItems = Object.entries(data?.by_type ?? {})
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({
      label: TYPE_META[k as keyof typeof TYPE_META]?.label ?? k,
      value: v,
      color: TYPE_COLOR[k as keyof typeof TYPE_COLOR] ?? "var(--mute)",
    }));

  return (
    <div className="flex flex-col gap-6">
      {err && (
        <div className="rounded-xl border border-err/30 bg-err-bg px-4 py-3 text-sm text-err">
          加载统计失败：{err}（请确认服务在运行且可访问 /stats/detail）
        </div>
      )}

      {/* 指标卡 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-6">
        <StatCard label="总任务" delay={0}>
          <AnimatedNumber value={s?.total ?? 0} format={(v) => String(Math.round(v))} className="tnum font-mono text-2xl font-semibold text-ink" />
        </StatCard>
        <StatCard label="完成" delay={0.03}>
          <AnimatedNumber value={s?.done ?? 0} format={(v) => String(Math.round(v))} className="tnum font-mono text-2xl font-semibold text-ok" />
        </StatCard>
        <StatCard label="失败" delay={0.06}>
          <AnimatedNumber value={s?.failed ?? 0} format={(v) => String(Math.round(v))} className="tnum font-mono text-2xl font-semibold text-err" />
        </StatCard>
        <StatCard label="成功率" delay={0.09}>
          <span className="tnum font-mono text-2xl font-semibold text-ink">{s ? fmt(s.success_rate, 0) : "–"}</span>
          <span className="text-xs text-ink-3">%</span>
        </StatCard>
        <StatCard label="平均耗时" delay={0.12}>
          <AnimatedNumber value={s?.avg_run_seconds ?? 0} format={(v) => fmt(v)} className="tnum font-mono text-2xl font-semibold text-ink" />
          <span className="text-xs text-ink-3">s</span>
        </StatCard>
        <StatCard label="类型占比" delay={0.15}>
          <span className="text-[13px] leading-6 text-ink-2">
            {typeItems.length
              ? typeItems.map((t) => `${t.label} ${t.value}`).join(" · ")
              : "–"}
          </span>
        </StatCard>
      </div>

      {/* 图表 row 1：趋势（2/3）+ 状态分布（1/3） */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card delay={0.18} className="p-5 lg:col-span-2">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium text-ink-2">任务量趋势</h2>
            <Select
              value={String(hours)}
              onValueChange={(v) => setHours(parseInt(v, 10) || 24)}
              ariaLabel="统计时间范围"
              className="w-32"
              options={RANGES.map((r) => ({ value: String(r.v), label: r.label }))}
            />
          </div>
          <LineChart
            height={240}
            decimals={0}
            labels={labels}
            series={[
              { name: "全部", color: CHART.total, data: (data?.series ?? []).map((p) => p.total) },
              { name: "完成", color: CHART.done, data: (data?.series ?? []).map((p) => p.done) },
              { name: "失败", color: CHART.failed, data: (data?.series ?? []).map((p) => p.failed) },
            ]}
          />
        </Card>
        <Card delay={0.21} className="p-5">
          <h2 className="mb-3 text-sm font-medium text-ink-2">状态分布</h2>
          <div className="flex h-[calc(100%-1.5rem)] items-center justify-center py-4">
            <DonutChart items={statusItems} centerLabel="个任务" />
          </div>
        </Card>
      </div>

      {/* 图表 row 2：类型分布 + 平均耗时趋势 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card delay={0.24} className="p-5">
          <h2 className="mb-3 text-sm font-medium text-ink-2">类型分布</h2>
          <div className="flex items-center justify-center py-4">
            <DonutChart items={typeItems} size={190} centerLabel="个任务" />
          </div>
        </Card>
        <Card delay={0.27} className="p-5 lg:col-span-2">
          <h2 className="mb-3 text-sm font-medium text-ink-2">平均耗时趋势</h2>
          <LineChart
            height={210}
            unit="s"
            labels={labels}
            series={[
              { name: "平均耗时", color: CHART.avg, data: (data?.series ?? []).map((p) => p.avg_run), fill: true },
            ]}
          />
        </Card>
      </div>
    </div>
  );
}

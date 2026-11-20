import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";

import { fetchStats } from "../api";
import { CHART } from "../constants";
import { fmt } from "../lib/ui";
import type { Monitor, MonitorHistory, StatsBrief } from "../types";
import { Card } from "./Card";
import { StatCard } from "./StatCard";
import { AnimatedNumber } from "./AnimatedNumber";
import { LineChart } from "./charts/LineChart";
import { DevicePanel } from "./DevicePanel";
import { LiveTaskTable } from "./LiveTaskTable";
import { LogConsole } from "./LogConsole";
import type { TabId } from "./Header";
import type { ToastPush } from "./toastTypes";

function Meter({ value, color }: { value: number; color: string }) {
  const p = Math.max(0, Math.min(100, value));
  return (
    <div className="mt-2 h-1 overflow-hidden rounded bg-surface-2">
      <div
        className="h-full rounded transition-[width] duration-500"
        style={{ width: `${p}%`, background: color }}
      />
    </div>
  );
}

/** 监控面板（原 index.html）：指标卡 + 实时图表 + 设备 + 任务流 + 日志。 */
export function DashboardPage({
  monitor,
  history,
  push,
  onNavigate,
}: {
  monitor: Monitor | null;
  history: MonitorHistory;
  push: ToastPush;
  onNavigate: (t: TabId) => void;
}) {
  // 今日统计（/stats?hours=24）：进页拉一次
  const [brief, setBrief] = useState<StatsBrief | null>(null);
  useEffect(() => {
    let alive = true;
    fetchStats(24)
      .then((d) => alive && setBrief(d))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const gpuPct = (monitor?.gpu_load ?? 0) * 100;
  const gpuOver = monitor ? gpuPct > monitor.gpu_threshold * 100 : false;

  return (
    <div className="flex flex-col gap-6">
      {/* 指标卡 */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatCard
          label="CPU"
          delay={0}
          sub={<Meter value={monitor?.cpu_percent ?? 0} color={CHART.cpu} />}
        >
          <AnimatedNumber value={monitor?.cpu_percent ?? 0} format={(v) => fmt(v)} className="tnum font-mono text-2xl font-semibold text-ink" />
          <span className="text-xs text-ink-3">%</span>
        </StatCard>
        <StatCard
          label="RAM 可用"
          delay={0.03}
          sub={
            <>
              总量 <span className="tnum font-mono text-ink-2">{fmt(monitor?.ram_total_gb)}</span> GB
            </>
          }
        >
          <AnimatedNumber value={monitor?.ram_available_gb ?? 0} format={(v) => fmt(v)} className="tnum font-mono text-2xl font-semibold text-ink" />
          <span className="text-xs text-ink-3">GB</span>
        </StatCard>
        <StatCard
          label="GPU"
          delay={0.06}
          sub={<Meter value={gpuPct} color={gpuOver ? "var(--err)" : CHART.gpu} />}
        >
          <AnimatedNumber value={gpuPct} format={(v) => fmt(v)} className="tnum font-mono text-2xl font-semibold text-ink" />
          <span className="text-xs text-ink-3">%</span>
        </StatCard>
        <StatCard
          label="VRAM 可用"
          delay={0.09}
          sub={
            <>
              总量 <span className="tnum font-mono text-ink-2">{fmt(monitor?.gpu_total_gb)}</span> GB
            </>
          }
        >
          <AnimatedNumber value={monitor?.gpu_free_gb ?? 0} format={(v) => fmt(v)} className="tnum font-mono text-2xl font-semibold text-ink" />
          <span className="text-xs text-ink-3">GB</span>
        </StatCard>
        <StatCard
          label="队列"
          delay={0.12}
          sub={
            <>
              运行 <span className="tnum font-mono text-ink-2">{monitor?.running ?? 0}</span> · 有效并发{" "}
              <span className="tnum font-mono text-ink-2">{monitor?.effective_concurrent ?? "–"}</span>
            </>
          }
        >
          <AnimatedNumber value={monitor?.queue_length ?? 0} format={(v) => String(Math.round(v))} className="tnum font-mono text-2xl font-semibold text-ink" />
          <span className="text-xs text-ink-3">等待中</span>
        </StatCard>
        <StatCard
          label="今日统计"
          delay={0.15}
          sub={
            <>
              共 <span className="tnum font-mono text-ink-2">{brief?.total ?? 0}</span> 个任务
            </>
          }
        >
          <span className="tnum font-mono text-2xl font-semibold text-ink">
            {brief ? fmt(brief.success_rate, 0) : "–"}
          </span>
          <span className="text-xs text-ink-3">成功率</span>
        </StatCard>
      </div>

      {/* 实时图表 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card delay={0.18} className="p-4">
          <div className="mb-2 flex items-center justify-between px-1">
            <h2 className="text-sm font-medium text-ink-2">CPU / GPU 负载</h2>
            <span className="text-[11px] text-ink-3">最近 120s</span>
          </div>
          <div className="flex items-baseline gap-3 px-1 pb-1">
            <div className="flex-1">
              <Meter value={monitor?.cpu_percent ?? 0} color={CHART.cpu} />
            </div>
            <div className="flex-1">
              <Meter value={gpuPct} color={gpuOver ? "var(--err)" : CHART.gpu} />
            </div>
          </div>
          <LineChart
            height={190}
            yMax={100}
            unit="%"
            labels={history.labels}
            series={[
              { name: "CPU %", color: CHART.cpu, data: history.cpu, fill: true },
              { name: "GPU %", color: CHART.gpu, data: history.gpu, fill: true },
            ]}
          />
        </Card>

        <Card delay={0.21} className="p-4">
          <div className="mb-2 flex items-center justify-between px-1">
            <h2 className="text-sm font-medium text-ink-2">队列与并发</h2>
            <span className="text-[11px] text-ink-3">动态并发（GPU 过载自动降）</span>
          </div>
          <LineChart
            height={190}
            decimals={0}
            labels={history.labels}
            series={[
              { name: "队列", color: CHART.queue, data: history.queue, fill: true },
              { name: "运行中", color: CHART.run, data: history.running },
              { name: "有效并发", color: CHART.eff, data: history.eff, dashed: true },
            ]}
          />
        </Card>
      </div>

      {/* 设备状态 */}
      <Card delay={0.24}>
        <DevicePanel />
      </Card>

      {/* 实时任务表 */}
      <Card delay={0.27}>
        <div className="flex flex-col gap-3 border-b border-line px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-sm font-medium text-ink-2">任务列表</h2>
          <button
            onClick={() => onNavigate("tasks")}
            className="inline-flex items-center gap-1 text-[11px] text-ink-2 transition-colors hover:text-accent"
          >
            查看全部任务 <ArrowRight size={12} />
          </button>
        </div>
        <LiveTaskTable push={push} />
      </Card>

      {/* 控制台日志 */}
      <Card delay={0.3}>
        <LogConsole />
      </Card>
    </div>
  );
}

import { motion } from "motion/react";
import {
  Monitor as MonitorIcon,
  Moon,
  Power,
  Settings,
  Sun,
  Waypoints,
} from "lucide-react";

import type { Monitor } from "../types";
import type { ThemeMode } from "../hooks/useTheme";
import { SC_META, THROTTLE_TEXT } from "../constants";
import { fmt } from "../lib/ui";
import { IconRadioGroup } from "./ui/RadioGroup";
import { Tooltip } from "./ui/Tooltip";

const spring = { type: "spring", stiffness: 420, damping: 32 } as const;

export type TabId = "dash" | "services" | "tasks" | "stats" | "docs";

export const TABS: Array<{ id: TabId; label: string }> = [
  { id: "dash", label: "监控面板" },
  { id: "services", label: "服务面板" },
  { id: "tasks", label: "全部任务" },
  { id: "stats", label: "统计" },
  { id: "docs", label: "API 文档" },
];

const PILL_BASE = "rounded-full border px-3 py-1.5 text-xs whitespace-nowrap";

/** 自检 pill 悬浮明细（对齐旧版 updateSelfCheck 的 title 拼装） */
function selfCheckTitle(m: Monitor | null): string {
  const sc = m?.self_check;
  if (!sc) return "上游自检：等待巡检…";
  const lines = (sc.items ?? []).map((a) => `[${a.level}] ${a.code}: ${a.msg}`);
  const ps = sc.progress_stream;
  if (ps) {
    const ago =
      ps.last_event_ago === null || ps.last_event_ago === undefined ? "–" : `${ps.last_event_ago}s`;
    lines.push(
      `进度订阅(/ws)：${ps.connected ? "已连接" : "未连接"}，最近事件 ${ago} 前` +
        (ps.queue_remaining === null || ps.queue_remaining === undefined
          ? ""
          : `，上游队列剩余 ${ps.queue_remaining}`),
    );
  }
  const probe = sc.probe;
  if (probe && probe.reachable !== undefined) {
    lines.push(
      `上游 /queue：${probe.reachable ? "可达" : "不可达"}` +
        `（执行中 ${(probe.running ?? []).length}，排队 ${(probe.pending ?? []).length}` +
        (probe.vram_free_gb ? `，显存可用 ${probe.vram_free_gb}GB` : "") +
        "）",
    );
  }
  return lines.length ? lines.join("\n") : "上游自检：核对 /queue 与 /ws 进度，未发现异常";
}

function Pill({ cls, title, children }: { cls: string; title?: string; children: React.ReactNode }) {
  return (
    <span className={`${PILL_BASE} ${cls} cursor-default`} title={title}>
      {children}
    </span>
  );
}

const THEME_OPTS: Array<{ value: ThemeMode; icon: typeof Sun; label: string }> = [
  { value: "light", icon: Sun, label: "浅色" },
  { value: "dark", icon: Moon, label: "深色" },
  { value: "system", icon: MonitorIcon, label: "跟随系统" },
];

export function Header({
  tab,
  onTab,
  theme,
  onTheme,
  monitor,
  wsConnected,
  onOpenConfig,
  onShutdown,
}: {
  tab: TabId;
  onTab: (t: TabId) => void;
  theme: ThemeMode;
  onTheme: (m: ThemeMode) => void;
  monitor: Monitor | null;
  wsConnected: boolean;
  onOpenConfig: () => void;
  onShutdown: () => void;
}) {
  const reason = monitor?.throttle_reason ?? "normal";
  const overload = reason !== "normal";
  const sc = monitor?.self_check;
  const scMeta = SC_META[sc?.level ?? "ok"] ?? SC_META.ok;

  return (
    <header className="sticky top-0 z-30 border-b border-line/60 bg-bg/80 backdrop-blur">
      <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        {/* 品牌 */}
        <div className="flex items-center gap-3">
          <motion.div
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={spring}
            className="flex size-9 shrink-0 items-center justify-center rounded-xl border border-accent/30 bg-accent-soft text-accent"
          >
            <Waypoints size={19} />
          </motion.div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-ink">TaskHub</h1>
            <p className="text-xs text-ink-3">本地 LLM 调度</p>
          </div>
        </div>

        {/* 状态 pills */}
        <div className="flex flex-wrap items-center gap-2">
          <Pill
            title={`阈值 ${fmt(monitor?.gpu_threshold, 2)} · GPU 负载 ${fmt((monitor?.gpu_load ?? 0) * 100, 1)}%`}
            cls={
              overload
                ? "border-err/40 bg-err-bg text-err"
                : "border-ok/40 bg-ok-bg text-ok"
            }
          >
            <span
              className={`mr-1.5 inline-block size-1.5 rounded-full ${overload ? "bg-err" : "bg-ok animate-breathe"}`}
            />
            {monitor ? THROTTLE_TEXT[reason] ?? "运行正常" : "探测后端…"}
          </Pill>

          <Pill title={selfCheckTitle(monitor)} cls={`border-line bg-surface-2 ${scMeta.cls}`}>
            自检：{sc ? scMeta.label : "–"}
            {sc && sc.count ? ` ${sc.count}` : ""}
          </Pill>

          <Pill
            title="WebSocket /ws/monitor 连接状态"
            cls={
              wsConnected
                ? "border-ok/40 bg-ok-bg text-ok"
                : "border-err/40 bg-err-bg text-err"
            }
          >
            {wsConnected ? "实时已连接" : "重连中…"}
          </Pill>

          <Tooltip content="查看当前配置">
            <button
              onClick={onOpenConfig}
              className={`${PILL_BASE} border-line bg-surface-2 text-ink-2 transition-colors hover:border-line-strong hover:text-ink`}
            >
              <Settings size={12} className="mr-1 inline align-[-1px]" />
              配置
            </button>
          </Tooltip>

          <Tooltip content="关闭 TaskHub 服务（页面将不可用）">
            <button
              onClick={onShutdown}
              className={`${PILL_BASE} border-err/40 bg-err-bg text-err transition-colors hover:border-err`}
            >
              <Power size={12} className="mr-1 inline align-[-1px]" />
              关闭服务
            </button>
          </Tooltip>

          {/* 主题三态切换（Radix RadioGroup） */}
          <IconRadioGroup
            value={theme}
            onValueChange={onTheme}
            options={THEME_OPTS}
            ariaLabel="主题"
          />
        </div>
      </div>

      {/* 页签导航 */}
      <nav className="mx-auto max-w-7xl px-4 sm:px-6" aria-label="页面导航">
        <div className="flex gap-1 overflow-x-auto pb-2">
          {TABS.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => onTab(t.id)}
                className={`relative shrink-0 rounded-full px-4 py-1.5 text-sm transition-colors ${
                  active ? "text-on-accent" : "text-ink-2 hover:text-ink"
                }`}
              >
                {active && (
                  <motion.span
                    layoutId="tab-pill"
                    transition={spring}
                    className="absolute inset-0 rounded-full bg-accent"
                  />
                )}
                <span className="relative z-10">{t.label}</span>
              </button>
            );
          })}
        </div>
      </nav>
    </header>
  );
}

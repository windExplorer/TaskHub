/** 任务表格共享小件：类型/状态 chip、异常徽章、NSFW 结论徽章、comfyui 进度格 */
import { Info, OctagonX, TriangleAlert } from "lucide-react";

import { ANOM_LABEL, ANOM_STYLE, NSFW_META, STATUS_META, TYPE_META } from "../constants";
import type { Task, TaskAnomaly, TaskNsfw, TaskStatus, TaskType } from "../types";

export function StatusChip({ status }: { status: TaskStatus }) {
  const m = STATUS_META[status];
  if (!m) return <span className="text-xs text-ink-3">{status}</span>;
  return (
    <span className={`inline-flex rounded-md border px-2 py-0.5 text-xs ${m.cls}`}>{m.label}</span>
  );
}

export function TypeChip({ type }: { type: TaskType }) {
  const m = TYPE_META[type];
  if (!m) return <span className="text-xs text-ink-3">{type}</span>;
  return (
    <span className={`inline-flex rounded-md border px-2 py-0.5 text-xs ${m.cls}`}>{m.label}</span>
  );
}

/** 自检判定的任务级异常（图标 + 中文码，完整 msg 在 title） */
export function AnomalyBadge({ a }: { a: TaskAnomaly | null }) {
  if (!a) return null;
  const s = ANOM_STYLE[a.level] ?? ANOM_STYLE.warn;
  const Icon = a.level === "error" ? OctagonX : a.level === "warn" ? TriangleAlert : Info;
  return (
    <span
      title={a.msg}
      className={`mt-1 inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[10px] ${s.cls}`}
    >
      <Icon size={11} />
      {ANOM_LABEL[a.code] ?? a.code}
    </span>
  );
}

/** NSFW 送检结论徽章：检测未完成（available=false）或无结论不显示 level 时归为「未检出」 */
export function NsfwBadge({ n }: { n: TaskNsfw | null }) {
  if (!n) return null;
  const key = n.available === false || !n.level ? "unavailable" : String(n.level);
  const m = NSFW_META[key] ?? NSFW_META.unavailable;
  const score = typeof n.score === "number" ? ` ${n.score.toFixed(2)}` : "";
  const title = n.available === false
    ? `检测未完成：${n.error ?? "未知原因"}`
    : `level=${n.level} action=${n.action ?? "-"} score=${n.score ?? "-"}（最严一张，共 ${n.images_checked ?? 1} 张）`;
  return (
    <span
      title={title}
      className={`tnum mt-1 inline-flex items-center whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[10px] ${m.cls}`}
    >
      {m.label}
      {score}
    </span>
  );
}

/** comfyui 任务的真实进度（节点 x/总数 · 步 s/S · 百分比 + 细进度条） */
export function ProgressCell({ t }: { t: Task }) {
  const p = t.progress ?? {};
  if (t.task_type !== "comfyui" || !p.nodes_total) return null;
  const pct = Math.min(100, Math.round(((p.nodes_done ?? 0) / p.nodes_total) * 100));
  const step = p.step_total ? ` · 步 ${p.step ?? 0}/${p.step_total}` : "";
  return (
    <div className="mt-1">
      <div className="tnum whitespace-nowrap font-mono text-[10px] text-ink-3">
        {p.nodes_done ?? 0}/{p.nodes_total} 节点{step} · {pct}%
      </div>
      <div className="mt-1 h-[3px] overflow-hidden rounded bg-surface-2">
        <div
          className="h-full rounded bg-info transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/** 运行秒单元格配色：自检判异常（超基线/卡住）时标红加粗，一眼看出哪个任务不对劲 */
export function runSecondsCls(t: Task): string {
  const lv = t.anomaly?.level;
  return lv === "warn" || lv === "error" ? "font-semibold text-err" : "text-ink-2";
}

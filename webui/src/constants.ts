/** 展示元数据：状态/类型色板、异常码与降级文案、图表配色（从旧版 4 页 JS 常量收拢） */
import type { TaskStatus, TaskType } from "./types";

/** 全站统一入场弹簧（nsfw-check 同款参数） */
export const spring = { type: "spring", stiffness: 300, damping: 28 } as const;

export interface ChipMeta {
  label: string;
  /** 状态 chip 的组合类名（底/字/边） */
  cls: string;
  /** 图例圆点 */
  dot: string;
}

export const STATUS_META: Record<TaskStatus, ChipMeta> = {
  queued: { label: "排队中", cls: "bg-mute-bg text-mute border-mute/40", dot: "bg-mute" },
  waiting: { label: "等待中", cls: "bg-warn-bg text-warn border-warn/40", dot: "bg-warn" },
  running: { label: "运行中", cls: "bg-info-bg text-info border-info/40", dot: "bg-info" },
  done: { label: "完成", cls: "bg-ok-bg text-ok border-ok/40", dot: "bg-ok" },
  failed: { label: "失败", cls: "bg-err-bg text-err border-err/40", dot: "bg-err" },
  timeout: { label: "超时", cls: "bg-warn-bg text-warn border-warn/40", dot: "bg-warn" },
  cancelled: { label: "已取消", cls: "bg-mute-bg text-mute border-mute/40", dot: "bg-mute" },
};

export const TYPE_META: Record<TaskType, { label: string; cls: string }> = {
  tts: { label: "语音", cls: "bg-info-bg text-info border-info/40" },
  comfyui: { label: "绘图", cls: "bg-accent-2-bg text-accent-2 border-accent-2/40" },
  nsfw: { label: "涩图检测", cls: "bg-warn-bg text-warn border-warn/40" },
};

/** 自检异常码 → 中文 */
export const ANOM_LABEL: Record<string, string> = {
  lost: "上游已丢弃",
  stalled: "疑似卡住",
  slow: "耗时超基线",
  slot_wait: "等槽位过久",
  upstream_external: "上游外部任务",
  upstream_vram_low: "上游显存不足",
  upstream_unreachable: "上游不可达",
  tts_not_ready: "语音后端未就绪",
};

export interface AnomMeta {
  label: string;
  cls: string;
}

/** anomaly 徽章按 level 着色 */
export const ANOM_STYLE: Record<string, AnomMeta> = {
  error: { label: "err", cls: "bg-err-bg text-err border-err/40" },
  warn: { label: "warn", cls: "bg-warn-bg text-warn border-warn/40" },
  info: { label: "info", cls: "bg-info-bg text-info border-info/40" },
};

/** NSFW 送检结论徽章（verdict.level → 文案/配色；available=false 视为「未检出」） */
export const NSFW_META: Record<string, ChipMeta> = {
  nsfw: { label: "NSFW", cls: "bg-err-bg text-err border-err/40", dot: "bg-err" },
  review: { label: "需复核", cls: "bg-warn-bg text-warn border-warn/40", dot: "bg-warn" },
  safe: { label: "安全", cls: "bg-ok-bg text-ok border-ok/40", dot: "bg-ok" },
  unavailable: { label: "未检出", cls: "bg-mute-bg text-mute border-mute/40", dot: "bg-mute" },
};

/** GPU 过载降并发原因 → 文案 */
export const THROTTLE_TEXT: Record<string, string> = {
  normal: "运行正常",
  gpu: "GPU 算力过载 · 降并发",
  vram: "显存不足 · 降并发",
  "gpu+vram": "GPU+显存告急 · 降并发",
};

/** 自检等级 → pill 文案与配色 */
export const SC_META: Record<string, { label: string; cls: string }> = {
  ok: { label: "正常", cls: "bg-ok-bg text-ok border-ok/40" },
  info: { label: "提示", cls: "bg-info-bg text-info border-info/40" },
  warn: { label: "异常", cls: "bg-warn-bg text-warn border-warn/40" },
  error: { label: "异常", cls: "bg-err-bg text-err border-err/40" },
};

/** 图表线条配色（引用语义变量，深浅主题自动跟随） */
export const CHART = {
  cpu: "var(--info)",
  gpu: "var(--accent-2)",
  queue: "var(--ok)",
  run: "var(--err)",
  eff: "var(--ink-3)",
  total: "var(--info)",
  done: "var(--ok)",
  failed: "var(--err)",
  avg: "var(--accent-2)",
} as const;

export const SHOW_OPTIONS = [5, 10, 20, 50, 100];
export const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

/** 图表用线条色（环形图/柱状分段） */
export const STATUS_COLOR: Record<TaskStatus, string> = {
  queued: "var(--mute)",
  waiting: "var(--warn)",
  running: "var(--info)",
  done: "var(--ok)",
  failed: "var(--err)",
  timeout: "var(--warn)",
  cancelled: "var(--mute)",
};
export const TYPE_COLOR: Record<TaskType, string> = {
  tts: "var(--info)",
  comfyui: "var(--accent-2)",
  nsfw: "var(--warn)",
};

/** 原生表单控件统一观感 */
export const selectCls =
  "rounded-lg border border-line bg-surface px-2.5 py-1.5 text-sm text-ink-2 outline-none transition-colors hover:border-line-strong focus:border-accent";
export const inputCls =
  "w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink-2 outline-none transition-colors placeholder:text-ink-3/70 hover:border-line-strong focus:border-accent";
export const btnCls =
  "rounded-lg border border-line bg-surface px-3.5 py-1.5 text-sm text-ink-2 transition-colors hover:border-line-strong hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-40";
export const btnPrimaryCls =
  "rounded-lg border border-accent/40 bg-accent-soft px-3.5 py-1.5 text-sm text-accent transition-colors hover:bg-accent hover:text-on-accent";

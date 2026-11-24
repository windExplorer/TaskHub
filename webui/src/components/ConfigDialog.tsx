import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import * as RDialog from "@radix-ui/react-dialog";
import { Check, Loader2, RotateCw, X } from "lucide-react";

import { fetchConfigRaw, fetchHealth, restartService, saveConfig } from "../api";
import type { ConfigSection } from "../types";
import { Switch } from "./ui/Switch";

const spring = { type: "spring", stiffness: 340, damping: 30 } as const;

type Draft = Record<string, ConfigSection>;

/** 可编辑字段：[key, label, 类型]。dict/voices 等复杂字段只在下方提示，不在此编辑。 */
type Field = [string, string, "str" | "num" | "bool"];
const SECTIONS: Array<{ key: string; title: string; fields: Field[] }> = [
  {
    key: "server",
    title: "服务 / 调度",
    fields: [
      ["host", "监听地址", "str"], ["port", "端口", "num"],
      ["max_concurrent", "最大并发", "num"], ["queue_wait", "排队超时(s)", "num"],
      ["infer_timeout", "推理超时(s)", "num"], ["db_path", "数据库", "str"],
      ["log_max_lines", "日志行数", "num"], ["frontend_dir", "前端目录", "str"],
      ["vram_cleanup_on_type_switch", "切类型清显存", "bool"],
    ],
  },
  {
    key: "tts",
    title: "TTS（CosyVoice）",
    fields: [
      ["base_url", "后端地址", "str"], ["sample_rate", "采样率(Hz)", "num"],
      ["voices_dir", "参考音频目录", "str"], ["voices_json", "音色映射文件", "str"],
    ],
  },
  {
    key: "comfyui",
    title: "ComfyUI",
    fields: [
      ["base_url", "后端地址", "str"], ["serialize_concurrent", "单飞并发", "num"],
      ["watch_interval", "轮询间隔(s)", "num"], ["watch_timeout", "跟踪超时(s)", "num"],
      ["watch_lost_grace", "丢失宽限(s)", "num"], ["watch_lost_confirm", "丢失确认次数", "num"],
      ["progress_stream", "订阅 /ws 进度", "bool"], ["client_id", "client_id", "str"],
      ["free_vram_on_type_switch", "切离时释放显存", "bool"],
    ],
  },
  {
    key: "monitoring",
    title: "监控",
    fields: [
      ["gpu_threshold", "GPU 阈值", "num"], ["vram_threshold", "显存比例下限", "num"],
      ["vram_min_free_gb", "显存绝对下限(GB)", "num"], ["interval_seconds", "采样间隔(s)", "num"],
    ],
  },
  {
    key: "inspector",
    title: "上游自检",
    fields: [
      ["enabled", "启用", "bool"], ["interval", "巡检周期(s)", "num"],
      ["slow_factor", "超基线系数", "num"], ["slow_min_seconds", "基线下限(s)", "num"],
      ["stall_seconds", "卡住告警(s)", "num"], ["history_hours", "历史窗口(h)", "num"],
      ["baseline_ttl", "基线缓存(s)", "num"], ["system_stats_every", "stats 巡检间隔(轮)", "num"],
    ],
  },
  {
    key: "nsfw",
    title: "NSFW 送检",
    fields: [
      ["enabled", "启用", "bool"], ["base_url", "检测服务地址", "str"],
      ["mode", "模式(label/block)", "str"], ["timeout", "检测超时(s)", "num"],
      ["queue_wait", "排队超时(s)", "num"],
      ["wait_seconds", "等待检测(s)", "num"], ["output_dir", "ComfyUI output 目录", "str"],
      ["prewarm", "送检前预热", "bool"],
    ],
  },
];

const inputCls =
  "w-full rounded-lg border border-line bg-surface-2 px-2 py-1 font-mono text-xs text-ink outline-none focus:border-accent";

function FieldRow({
  label, kind, value, onChange,
}: {
  label: string;
  kind: Field[2];
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (kind === "bool") {
    const on = value === true || value === "true";
    return (
      <div className="flex items-center justify-between gap-3 border-b border-line py-1.5">
        <span className="text-ink-2">{label}</span>
        <Switch checked={on} onCheckedChange={onChange} ariaLabel={label} />
      </div>
    );
  }
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line py-1.5">
      <span className="shrink-0 text-ink-2">{label}</span>
      <input
        className={inputCls}
        type={kind === "num" ? "number" : "text"}
        step={kind === "num" ? "any" : undefined}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => onChange(kind === "num" ? Number(e.target.value) : e.target.value)}
      />
    </div>
  );
}

export function ConfigDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [restartDone, setRestartDone] = useState(false);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    setErr(null);
    setSaved(false);
    setRestartDone(false);
    let alive = true;
    fetchConfigRaw()
      .then((c) => alive && setDraft(c as Draft))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      alive = false;
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function setField(section: string, key: string, value: unknown) {
    setDraft((d) => (d ? { ...d, [section]: { ...d[section], [key]: value } } : d));
    setSaved(false);
  }

  async function handleSave(override?: Draft) {
    const payload = override ?? draft;
    if (!payload) return;
    setSaving(true);
    setErr(null);
    try {
      await saveConfig(payload as Record<string, Record<string, unknown>>);
      setSaved(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleRestart() {
    setRestarting(true);
    setRestartDone(false);
    setErr(null);
    let oldTs = 0;
    try {
      const health = await fetchHealth();
      oldTs = health.started_at ?? 0;
    } catch {
      /* 用 0 兜底 */
    }
    try {
      await restartService();
    } catch {
      /* 重启时连接中断属正常，继续轮询 */
    }
    // 等旧进程退出（连接失败）→ 再等新进程起来且 started_at 变化
    const deadline = Date.now() + 60_000;
    let done = false;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 1200));
      try {
        const h = await fetchHealth();
        if (h.status === "ok" && (!oldTs || (h.started_at ?? 0) > oldTs)) {
          done = true;
          break;
        }
      } catch {
        /* 服务尚未恢复，继续等 */
      }
    }
    setRestartDone(done);
    setRestarting(false);
    if (!done) setErr("重启超时：60s 内未检测到新进程，请手动检查服务状态");
  }

  return (
    <RDialog.Root open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <RDialog.Portal>
        <RDialog.Overlay asChild>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.18 }}
            className="fixed inset-0 z-50 bg-black/45 p-4"
          />
        </RDialog.Overlay>
        <RDialog.Content asChild aria-describedby={undefined} onEscapeKeyDown={(e) => { if (restarting) e.preventDefault(); }}>
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            transition={spring}
            className="theme-t fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-card outline-none"
          >
            <div className="flex items-center justify-between border-b border-line px-6 py-4">
              <div>
                <RDialog.Title className="text-base font-semibold text-ink">配置</RDialog.Title>
                <RDialog.Description className="mt-0.5 text-[11px] text-ink-3">
                  业务开关即时生效；其余字段保存后需重启服务生效
                </RDialog.Description>
              </div>
              <RDialog.Close asChild>
                <button
                  aria-label="关闭"
                  className="rounded-full p-1.5 text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink"
                >
                  <X size={16} />
                </button>
              </RDialog.Close>
            </div>

            <div className="scroll-thin flex-1 overflow-y-auto px-6 py-4 text-[13px]">
              {err && (
                <div className="mb-3 rounded-xl border border-err/30 bg-err-bg px-4 py-2 text-sm text-err">
                  {err}
                </div>
              )}
              {!draft && !err && (
                <div className="flex items-center justify-center gap-2 py-10 text-sm text-ink-3">
                  <Loader2 size={15} className="animate-spin" /> 加载配置…
                </div>
              )}

              {draft && (
                <>
                  {/* 业务开关（即时生效） */}
                  <section className="mb-5 rounded-xl border border-accent/30 bg-accent-soft/40 p-4">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-accent">
                      业务开关 · 即时生效
                    </div>
                    <p className="mb-3 text-[11px] text-ink-3">
                      关闭后拒绝该类新任务（返回 503），进行中的任务不受影响。
                    </p>
                    {(["tts", "comfyui", "nsfw"] as const).map((t) => {
                      const tasksEnabled = (draft.server?.tasks_enabled ?? {}) as Record<string, boolean>;
                      const cur = tasksEnabled[t] ?? true;
                      const label = t === "tts" ? "TTS 语音" : t === "comfyui" ? "ComfyUI 绘图" : "涩图检测";
                      return (
                        <div key={t} className="flex items-center justify-between border-b border-line py-2 last:border-b-0">
                          <span className="font-medium text-ink-2">{label}</span>
                          <Switch
                            checked={cur}
                            onCheckedChange={() => {
                              const next = { ...(draft.server?.tasks_enabled ?? {}), [t]: !cur };
                              const nextDraft = { ...draft, server: { ...draft.server, tasks_enabled: next } };
                              setDraft(nextDraft);
                              setSaved(false);
                              void handleSave(nextDraft);
                            }}
                            ariaLabel={`${label}任务`}
                          />
                        </div>
                      );
                    })}
                  </section>

                  {SECTIONS.map(({ key, title, fields }) => (
                    <section key={key} className="mb-5">
                      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-accent">
                        {title}
                      </div>
                      {fields.map(([k, label, kind]) => (
                        <FieldRow
                          key={k}
                          label={label}
                          kind={kind}
                          value={draft[key]?.[k]}
                          onChange={(v) => setField(key, k, v)}
                        />
                      ))}
                    </section>
                  ))}
                  <p className="text-[11px] text-ink-3">
                    复杂字段（音色映射、token 等）请直接编辑 middle-station.yaml。
                  </p>
                </>
              )}
            </div>

            <div className="flex items-center gap-2 border-t border-line px-6 py-3">
              <button
                onClick={() => void handleSave()}
                disabled={!draft || saving}
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-on-accent transition-opacity disabled:opacity-50"
              >
                {saving ? <Loader2 size={13} className="animate-spin" /> : saved ? <Check size={13} /> : null}
                {saved ? "已保存" : "保存配置"}
              </button>
              <button
                onClick={() => void handleRestart()}
                disabled={restarting}
                className="inline-flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs font-medium text-ink-2 transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
              >
                {restarting ? <Loader2 size={13} className="animate-spin" /> : restartDone ? <Check size={13} /> : <RotateCw size={13} />}
                {restarting ? "重启中…" : restartDone ? "重启完成" : "重启服务"}
              </button>
              <span className="ml-auto text-[11px] text-ink-3">
                {saved ? "配置已写入 yaml，点击「重启服务」使其全部生效" : ""}
              </span>
            </div>
          </motion.div>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}

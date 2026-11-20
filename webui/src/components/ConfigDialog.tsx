import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";

import { fetchConfig } from "../api";
import type { ConfigSection, RuntimeConfig } from "../types";

const spring = { type: "spring", stiffness: 340, damping: 30 } as const;

/** 各配置节的展示字段（label 对齐旧版配置弹窗） */
const SECTIONS: Array<{ key: keyof Omit<RuntimeConfig, "config_file">; fields: Array<[string, string]> }> = [
  {
    key: "server",
    fields: [
      ["host", "监听地址"],
      ["port", "端口"],
      ["max_concurrent", "最大并发"],
      ["queue_wait", "排队超时(s)"],
      ["infer_timeout", "推理超时(s)"],
      ["db_path", "数据库"],
    ],
  },
  {
    key: "tts",
    fields: [
      ["base_url", "CosyVoice 后端"],
      ["sample_rate", "采样率(Hz)"],
      ["model_loaded", "模型加载"],
      ["voices_dir", "参考音频目录"],
      ["voices", "音色数"],
    ],
  },
  {
    key: "comfyui",
    fields: [
      ["base_url", "ComfyUI 后端"],
      ["client_id", "提交用 client_id"],
      ["serialize_concurrent", "单飞并发"],
      ["watch_interval", "轮询间隔(s)"],
      ["watch_timeout", "跟踪超时(s)"],
      ["watch_lost_grace", "丢失判定宽限(s)"],
      ["progress_stream", "订阅 /ws 进度"],
    ],
  },
  {
    key: "monitoring",
    fields: [
      ["gpu_threshold", "GPU 阈值"],
      ["interval_seconds", "采样间隔(s)"],
    ],
  },
  {
    key: "inspector",
    fields: [
      ["enabled", "自检启用"],
      ["interval", "巡检周期(s)"],
      ["slow_factor", "超基线系数"],
      ["slow_min_seconds", "基线阈值下限(s)"],
      ["stall_seconds", "无进度告警(s)"],
    ],
  },
];

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "–";
  return String(v);
}

export function ConfigDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [cfg, setCfg] = useState<RuntimeConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCfg(null);
    setErr(null);
    let alive = true;
    fetchConfig()
      .then((c) => alive && setCfg(c))
      .catch((e) => alive && setErr(e instanceof Error ? e.message : String(e)));
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      alive = false;
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) onClose();
          }}
        >
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 12 }}
            transition={spring}
            className="theme-t max-h-[80vh] w-full max-w-xl overflow-y-auto scroll-thin rounded-2xl border border-line bg-surface p-6 shadow-card"
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-ink">当前配置</h2>
              <button
                onClick={onClose}
                aria-label="关闭"
                className="rounded-full p-1.5 text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink"
              >
                <X size={16} />
              </button>
            </div>
            <p className="mb-4 text-xs text-ink-3">
              只读展示。修改请编辑仓库根目录{" "}
              <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-accent">
                middle-station.yaml
              </code>{" "}
              后重启服务。
            </p>

            {err && <div className="rounded-xl border border-err/30 bg-err-bg px-4 py-3 text-sm text-err">加载配置失败：{err}</div>}
            {!cfg && !err && <div className="py-8 text-center text-sm text-ink-3">加载中…</div>}

            {cfg && (
              <div className="text-[13px]">
                {SECTIONS.map(({ key, fields }) => {
                  const obj: ConfigSection = cfg[key] ?? {};
                  return (
                    <div key={key} className="mb-3">
                      <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-accent">
                        {key}
                      </div>
                      {fields.map(([k, label]) => (
                        <div
                          key={k}
                          className="flex items-center justify-between gap-3 border-b border-line py-1.5"
                        >
                          <span className="text-ink-2">{label}</span>
                          <span className="truncate text-right font-mono text-xs text-ink" title={show(obj[k])}>
                            {show(obj[k])}
                          </span>
                        </div>
                      ))}
                    </div>
                  );
                })}
                <div className="mt-3 text-[11px] text-ink-3">
                  配置文件：<span className="font-mono">{cfg.config_file}</span>（仓库根目录）
                </div>
              </div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

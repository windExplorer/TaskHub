import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import * as RDialog from "@radix-ui/react-dialog";
import { Loader2, Power, X } from "lucide-react";

import { shutdownService } from "../api";
import type { ToastPush } from "./toastTypes";

/** 关闭服务二次确认（Radix Dialog）+ 执行后的全屏"已停止"遮罩。 */
export function ShutdownDialog({
  open,
  onClose,
  push,
}: {
  open: boolean;
  onClose: () => void;
  push: ToastPush;
}) {
  const [busy, setBusy] = useState(false);
  const [stopped, setStopped] = useState(false);

  async function confirm() {
    setBusy(true);
    push("info", "正在关闭服务", "页面即将不可用");
    try {
      await shutdownService();
    } catch {
      /* 服务终止时连接中断属正常 */
    }
    setStopped(true);
    setBusy(false);
  }

  function close() {
    setStopped(false);
    setBusy(false);
    onClose();
  }

  return (
    <>
      <RDialog.Root open={open && !stopped} onOpenChange={(o) => { if (!o && !busy) close(); }}>
        <RDialog.Portal>
          <RDialog.Overlay asChild>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.15 }}
              className="fixed inset-0 z-50 bg-black/45 p-4"
            />
          </RDialog.Overlay>
          <RDialog.Content asChild aria-describedby={undefined} onEscapeKeyDown={(e) => { if (busy) e.preventDefault(); }}>
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 10 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              className="theme-t fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-line bg-surface p-6 shadow-card outline-none"
            >
              <div className="mb-3 flex items-center justify-between">
                <RDialog.Title className="flex items-center gap-2 text-base font-semibold text-ink">
                  <Power size={16} className="text-err" /> 关闭服务
                </RDialog.Title>
                <RDialog.Close asChild>
                  <button
                    disabled={busy}
                    className="rounded-full p-1.5 text-ink-3 hover:bg-surface-2 hover:text-ink disabled:opacity-40"
                    aria-label="取消"
                  >
                    <X size={16} />
                  </button>
                </RDialog.Close>
              </div>
              <RDialog.Description className="mb-5 text-sm text-ink-2">
                确认关闭 TaskHub？只会关闭中转站本身，ComfyUI / TTS-Serve / NSFW-Check 会继续运行。
                WebUI 将立即失联，需要用
                <code className="mx-1 rounded bg-surface-2 px-1 py-0.5 font-mono text-accent">start_daemon.bat</code>
                或
                <code className="mx-1 rounded bg-surface-2 px-1 py-0.5 font-mono text-accent">python main.py start --daemon</code>
                重新启动。
              </RDialog.Description>
              <div className="flex justify-end gap-2">
                <button
                  onClick={close}
                  disabled={busy}
                  className="rounded-lg border border-line px-3 py-1.5 text-xs text-ink-2 hover:border-line-strong disabled:opacity-40"
                >
                  取消
                </button>
                <button
                  onClick={() => void confirm()}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-err px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                >
                  {busy && <Loader2 size={13} className="animate-spin" />}
                  确认关闭
                </button>
              </div>
            </motion.div>
          </RDialog.Content>
        </RDialog.Portal>
      </RDialog.Root>

      {/* 服务已停止：全屏遮罩，避免 WS 无限重连造成"前端故障"错觉 */}
      <AnimatePresence>
        {stopped && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="fixed inset-0 z-[60] flex flex-col items-center justify-center gap-3 bg-bg/95"
          >
            <Power size={32} className="text-err" />
            <h2 className="text-lg font-semibold text-ink">服务已停止</h2>
            <p className="text-sm text-ink-3">重新启动后刷新此页面即可恢复</p>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

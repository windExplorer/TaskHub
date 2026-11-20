import { useCallback, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from "lucide-react";

export type ToastKind = "error" | "success" | "info" | "warn";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  detail?: string;
}

const KIND_STYLE: Record<ToastKind, { icon: typeof Info; cls: string }> = {
  error: { icon: CircleAlert, cls: "border-l-err text-err" },
  success: { icon: CircleCheck, cls: "border-l-ok text-ok" },
  warn: { icon: TriangleAlert, cls: "border-l-warn text-warn" },
  info: { icon: Info, cls: "border-l-accent text-accent" },
};

export function useToasts() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (kind: ToastKind, title: string, detail?: string) => {
      const id = ++seq.current;
      setToasts((list) => [...list.slice(-4), { id, kind, title, detail }]);
      window.setTimeout(() => dismiss(id), 5000);
    },
    [dismiss],
  );

  return { toasts, push, dismiss };
}

export function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-80 flex-col gap-2">
      <AnimatePresence>
        {toasts.map((t) => {
          const { icon: Icon, cls } = KIND_STYLE[t.kind];
          return (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, x: 48, scale: 0.96 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 48, scale: 0.96 }}
              transition={{ type: "spring", stiffness: 380, damping: 30 }}
              className={`pointer-events-auto flex items-start gap-2.5 rounded-xl border border-line border-l-4 bg-surface p-3 shadow-card ${cls}`}
            >
              <Icon size={18} className="mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-ink">{t.title}</p>
                {t.detail && <p className="mt-0.5 text-xs text-ink-3">{t.detail}</p>}
              </div>
              <button
                aria-label="关闭提示"
                onClick={() => onDismiss(t.id)}
                className="shrink-0 rounded p-0.5 text-ink-3 hover:bg-surface-2 hover:text-ink"
              >
                <X size={14} />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

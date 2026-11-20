import { AnimatePresence, motion } from "motion/react";
import * as RDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";

/** Radix Dialog + motion 动画封装：保留项目主题外观与弹簧动效，补齐 aria/焦点管理/Esc 关闭。 */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  maxWidth = "max-w-2xl",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  maxWidth?: string;
}) {
  return (
    <AnimatePresence>
      {open && (
        <RDialog.Root open={open} onOpenChange={onOpenChange}>
          <RDialog.Portal forceMount>
            <RDialog.Overlay asChild forceMount>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
                className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
              />
            </RDialog.Overlay>
            <RDialog.Content asChild forceMount>
              <motion.div
                initial={{ opacity: 0, scale: 0.95, y: 12 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95, y: 12 }}
                transition={{ type: "spring", stiffness: 340, damping: 30 }}
                className={`fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[calc(100%-2rem)] ${maxWidth} -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-line bg-surface p-6 shadow-card outline-none`}
              >
                <div className="mb-4 flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <RDialog.Title className="text-base font-semibold text-ink">{title}</RDialog.Title>
                    <RDialog.Description className={description ? "mt-1 text-[13px] text-ink-3" : "sr-only"}>
                      {description || title}
                    </RDialog.Description>
                  </div>
                  <RDialog.Close className="shrink-0 rounded-lg p-1 text-ink-3 transition hover:bg-surface-2 hover:text-ink" aria-label="关闭">
                    <X size={18} />
                  </RDialog.Close>
                </div>
                {children}
              </motion.div>
            </RDialog.Content>
          </RDialog.Portal>
        </RDialog.Root>
      )}
    </AnimatePresence>
  );
}

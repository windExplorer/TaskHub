import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "motion/react";

import { fmtClock } from "../lib/ui";
import { useWsLogs } from "../hooks/useWsLogs";

function levelCls(level: string): string {
  if (level === "ERROR") return "text-err";
  if (level === "WARNING") return "text-warn";
  return "text-ink-2";
}

/** 实时日志控制台：WS /ws/logs，最多 200 行，自动滚到底部。 */
export function LogConsole() {
  const { lines, connected } = useWsLogs();
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div>
      <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
        <h2 className="text-sm font-medium text-ink-2">控制台日志</h2>
        <div className="flex items-center gap-2">
          <span
            className={`size-1.5 rounded-full ${
              connected ? "bg-ok animate-breathe" : "bg-err"
            }`}
          />
          <span className="text-[11px] text-ink-3">{connected ? "实时" : "重连中…"}</span>
        </div>
      </div>
      <div
        ref={boxRef}
        className="h-64 overflow-y-auto scroll-thin px-5 py-3 font-mono text-xs leading-6"
      >
        {lines.length === 0 ? (
          <div className="text-ink-3">等待日志…</div>
        ) : (
          <AnimatePresence initial={false}>
            {lines.map((l, i) => (
              <motion.div
                key={`${l.ts}-${i}`}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2 }}
                className="whitespace-pre-wrap"
              >
                <span className="text-ink-3">{fmtClock(l.ts * 1000)}</span>{" "}
                <span className={levelCls(l.level)}>[{l.level}]</span>{" "}
                <span className={levelCls(l.level)}>{l.message}</span>
              </motion.div>
            ))}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}

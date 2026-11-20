import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ChevronDown, ChevronUp, RefreshCw } from "lucide-react";

import { fetchLogsTail } from "../api";
import { fmtClock } from "../lib/ui";
import { useWsLogs } from "../hooks/useWsLogs";
import { Tooltip } from "./ui/Tooltip";

function levelCls(level: string): string {
  if (level === "ERROR") return "text-err";
  if (level === "WARNING") return "text-warn";
  return "text-ink-2";
}

/** 实时日志控制台：WS /ws/logs + 落盘历史回填（/logs/tail），最多 200 行，自动滚底。 */
export function LogConsole() {
  const { lines, connected } = useWsLogs();
  const boxRef = useRef<HTMLDivElement>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [histOpen, setHistOpen] = useState(false);
  const [histLoading, setHistLoading] = useState(false);
  const [histFile, setHistFile] = useState("");

  const loadHistory = () => {
    setHistLoading(true);
    fetchLogsTail(200)
      .then((d) => {
        setHistory(d.lines ?? []);
        setHistFile(d.file ?? "");
      })
      .catch(() => setHistory([]))
      .finally(() => setHistLoading(false));
  };

  useEffect(() => {
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

      {/* 落盘历史（daemon/重启后的控制台输出，重启后仍可查） */}
      <div className="border-b border-line">
        <div className="flex w-full items-center justify-between px-5 py-2 text-[11px] text-ink-3">
          <button
            onClick={() => {
              setHistOpen((v) => !v);
              if (!histOpen) loadHistory();
            }}
            className="flex min-w-0 items-center gap-1.5 transition-colors hover:text-ink"
          >
            <span className="truncate">
              历史日志（落盘）{history.length ? ` · ${history.length} 行` : ""}
            </span>
            {histFile && <span className="truncate font-mono text-ink-3/70">{histFile}</span>}
            {histOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
          <Tooltip content="刷新历史">
            <button
              onClick={() => loadHistory()}
              aria-label="刷新历史"
              className="rounded p-1 hover:bg-surface-2"
            >
              <RefreshCw size={12} className={histLoading ? "animate-spin" : ""} />
            </button>
          </Tooltip>
        </div>
        <AnimatePresence initial={false}>
          {histOpen && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="max-h-64 overflow-y-auto scroll-thin bg-surface-2/40 px-5 py-2 font-mono text-[11px] leading-5">
                {history.length === 0 ? (
                  <div className="text-ink-3">暂无落盘日志</div>
                ) : (
                  history.map((line, i) => (
                    <div key={i} className="whitespace-pre-wrap text-ink-2">
                      {line}
                    </div>
                  ))
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
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

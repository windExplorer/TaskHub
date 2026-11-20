import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, X } from "lucide-react";

import { ApiFailure, cancelTask, promoteTask } from "../api";
import { SHOW_OPTIONS, selectCls } from "../constants";
import { fmt, fmtTime } from "../lib/ui";
import { useWsTasks } from "../hooks/useWsTasks";
import type { ToastPush } from "./toastTypes";
import { AnomalyBadge, ProgressCell, StatusChip, TypeChip, runSecondsCls } from "./taskBits";

const MAX_SHOW = 100;

/** 监控面板的实时任务表（WS 驱动）：排队/运行秒每秒本地自增，queued 行可插队/取消。 */
export function LiveTaskTable({ push }: { push: ToastPush }) {
  const { list, total } = useWsTasks();
  const [showAll, setShowAll] = useState(false);
  const [defaultShow, setDefaultShow] = useState<number>(() => {
    const v = parseInt(localStorage.getItem("taskhub:default-show") ?? "20", 10);
    return SHOW_OPTIONS.includes(v) ? v : 20;
  });
  const [now, setNow] = useState(() => Date.now() / 1000);

  // 运行中/排队中任务的耗时每秒本地递增（服务端只在状态变更时推送快照）
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, []);

  const changeShow = (v: number) => {
    setDefaultShow(v);
    setShowAll(false);
    try {
      localStorage.setItem("taskhub:default-show", String(v));
    } catch {
      /* 忽略隐私模式 */
    }
  };

  const act = async (taskId: string, op: "promote" | "cancel") => {
    try {
      if (op === "promote") {
        await promoteTask(taskId);
        push("success", "已插队", `${taskId} 已提到队列最前`);
      } else {
        await cancelTask(taskId);
        push("success", "已取消", taskId);
      }
    } catch (e) {
      if (e instanceof ApiFailure && e.status === 404) {
        push("warn", "操作无效", "任务不存在或已不在排队中");
      } else {
        push("error", "操作失败", e instanceof Error ? e.message : String(e));
      }
    }
  };

  const visible = showAll ? list.slice(0, MAX_SHOW) : list.slice(0, defaultShow);

  return (
    <div>
      {/* 工具条：计数 + 默认展示条数（localStorage 持久化，旧版同款） */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 py-2.5">
        <span className="text-[11px] text-ink-3">
          共 {total} 个（显示 {visible.length}）
        </span>
        <label className="flex items-center gap-2 text-[11px] text-ink-3">
          默认展示
          <select
            value={defaultShow}
            onChange={(e) => changeShow(parseInt(e.target.value, 10))}
            className={selectCls}
          >
            {SHOW_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {v} 条
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="overflow-x-auto scroll-thin">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-ink-3">
              <th className="px-5 py-2.5">任务 ID</th>
              <th className="px-3 py-2.5">类型</th>
              <th className="px-3 py-2.5 text-right">字数</th>
              <th className="px-3 py-2.5">状态</th>
              <th className="px-3 py-2.5 text-right">排队</th>
              <th className="px-3 py-2.5 text-right">运行</th>
              <th className="px-3 py-2.5">创建时间</th>
              <th className="px-3 py-2.5">完成时间</th>
              <th className="px-5 py-2.5 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-5 py-8 text-center text-ink-3">
                  暂无任务
                </td>
              </tr>
            ) : (
              visible.map((t) => {
                const canAct = t.status === "queued";
                const qSec = t.started_at
                  ? Math.max(0, t.started_at - t.created_at)
                  : Math.max(0, now - t.created_at);
                const rSec =
                  t.status === "running" || t.status === "waiting"
                    ? Math.max(0, now - (t.started_at ?? 0))
                    : t.run_seconds;
                return (
                  <tr
                    key={t.task_id}
                    className="border-b border-line transition-colors last:border-0 hover:bg-surface-2/60"
                  >
                    <td className="max-w-[180px] truncate px-5 py-2.5 font-mono text-xs text-ink-2" title={t.task_id}>
                      {t.task_id}
                    </td>
                    <td className="px-3 py-2.5">
                      <TypeChip type={t.task_type} />
                    </td>
                    <td className="tnum px-3 py-2.5 text-right font-mono text-xs text-ink-2">
                      {t.task_type === "tts" && t.text_len ? `${t.text_len}字` : "–"}
                    </td>
                    <td className="px-3 py-2.5">
                      <StatusChip status={t.status} />
                      <AnomalyBadge a={t.anomaly} />
                      <ProgressCell t={t} />
                    </td>
                    <td className="tnum px-3 py-2.5 text-right font-mono text-xs text-ink-2">
                      {fmt(qSec)}s
                    </td>
                    <td className={`tnum px-3 py-2.5 text-right font-mono text-xs ${runSecondsCls(t)}`}>
                      {fmt(rSec)}s
                    </td>
                    <td className="tnum px-3 py-2.5 font-mono text-xs text-ink-2">{fmtTime(t.created_at)}</td>
                    <td className="tnum px-3 py-2.5 font-mono text-xs text-ink-2">{fmtTime(t.finished_at)}</td>
                    <td className="px-5 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {canAct && (
                          <>
                            <button
                              onClick={() => void act(t.task_id, "promote")}
                              title="插队到最前"
                              className="inline-flex items-center gap-0.5 rounded-md border border-line px-2 py-1 text-xs text-ink-2 transition-colors hover:border-accent/40 hover:text-accent"
                            >
                              <ArrowUp size={12} /> 插队
                            </button>
                            <button
                              onClick={() => void act(t.task_id, "cancel")}
                              title="取消任务"
                              className="rounded-md border border-line p-1.5 text-ink-2 transition-colors hover:border-err/40 hover:text-err"
                            >
                              <X size={12} />
                            </button>
                          </>
                        )}
                        {t.error && (
                          <span className="ml-1 max-w-[160px] truncate font-mono text-[10px] text-ink-3" title={t.error}>
                            {t.error.slice(0, 24)}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <AnimatePresence>
        {list.length > defaultShow && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden text-center"
          >
            <button
              onClick={() => setShowAll((v) => !v)}
              className="my-2 rounded-full border border-line px-3 py-1 text-xs text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
            >
              {showAll
                ? `收起（显示最新 ${defaultShow} 条）`
                : `展开全部（共 ${Math.min(list.length, MAX_SHOW)} 条）`}
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

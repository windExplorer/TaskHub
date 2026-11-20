import { useCallback, useEffect, useState } from "react";

import { ApiFailure, fetchTasks } from "../api";
import { STATUS_META, TYPE_META, btnCls, btnPrimaryCls, inputCls, selectCls } from "../constants";
import { fmt, fmtTimeFull } from "../lib/ui";
import type { TasksPageData } from "../types";
import { Card } from "./Card";
import { StatusChip, TypeChip } from "./taskBits";

/** 全部任务（原 tasks.html）：筛选 + 分页历史表（SQLite 持久化）。 */
export function TasksPage() {
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [keyword, setKeyword] = useState("");
  const [applied, setApplied] = useState({ type: "", status: "", keyword: "" });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [data, setData] = useState<TasksPageData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await fetchTasks({
        page,
        page_size: pageSize,
        task_type: applied.type,
        status: applied.status,
        keyword: applied.keyword,
      });
      setData(d);
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiFailure ? e.detail : String(e));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, applied]);

  useEffect(() => {
    void load();
  }, [load]);

  // 类型/状态下拉：变更即生效并回到第一页
  const onType = (v: string) => {
    setType(v);
    setPage(1);
    setApplied((a) => ({ ...a, type: v }));
  };
  const onStatus = (v: string) => {
    setStatus(v);
    setPage(1);
    setApplied((a) => ({ ...a, status: v }));
  };
  const query = () => {
    setPage(1);
    setApplied({ type, status, keyword: keyword.trim() });
  };
  const reset = () => {
    setType("");
    setStatus("");
    setKeyword("");
    setPage(1);
    setApplied({ type: "", status: "", keyword: "" });
  };

  const pages = data?.pages ?? 1;

  return (
    <div className="flex flex-col gap-5">
      {/* 筛选区 */}
      <Card className="p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-[11px] text-ink-3">任务类型</span>
            <select value={type} onChange={(e) => onType(e.target.value)} className={selectCls}>
              <option value="">全部</option>
              {Object.entries(TYPE_META).map(([k, m]) => (
                <option key={k} value={k}>{m.label}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-[11px] text-ink-3">状态</span>
            <select value={status} onChange={(e) => onStatus(e.target.value)} className={selectCls}>
              <option value="">全部</option>
              {Object.entries(STATUS_META).map(([k, m]) => (
                <option key={k} value={k}>{m.label}</option>
              ))}
            </select>
          </label>
          <label className="block w-full sm:min-w-[220px] sm:flex-1">
            <span className="mb-1 block text-[11px] text-ink-3">关键词（任务ID / 错误信息）</span>
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && query()}
              placeholder="搜索…"
              className={inputCls}
            />
          </label>
          <button onClick={query} className={btnPrimaryCls}>查询</button>
          <button onClick={reset} className={btnCls}>重置</button>
        </div>
      </Card>

      {/* 历史表 */}
      <Card>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <span className="text-xs text-ink-3">共 {data?.total ?? 0} 条</span>
          <label className="flex items-center gap-2 text-xs text-ink-2">
            每页
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(parseInt(e.target.value, 10) || 20);
                setPage(1);
              }}
              className={selectCls}
            >
              {[10, 20, 50, 100].map((v) => (
                <option key={v} value={v}>{v} 条</option>
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
                <th className="px-5 py-2.5">错误</th>
              </tr>
            </thead>
            <tbody>
              {err ? (
                <tr>
                  <td colSpan={9} className="px-5 py-10 text-center text-err">加载失败：{err}</td>
                </tr>
              ) : loading && !data ? (
                <tr>
                  <td colSpan={9} className="px-5 py-10 text-center text-ink-3">加载中…</td>
                </tr>
              ) : !data || data.tasks.length === 0 ? (
                <tr>
                  <td colSpan={9} className="px-5 py-10 text-center text-ink-3">没有符合条件的任务</td>
                </tr>
              ) : (
                data.tasks.map((t) => (
                  <tr
                    key={t.task_id}
                    className="border-b border-line transition-colors last:border-0 hover:bg-surface-2/60"
                  >
                    <td className="max-w-[180px] truncate px-5 py-2.5 font-mono text-xs text-ink-2" title={t.task_id}>
                      {t.task_id}
                    </td>
                    <td className="px-3 py-2.5"><TypeChip type={t.task_type} /></td>
                    <td className="tnum px-3 py-2.5 text-right font-mono text-xs text-ink-2">
                      {t.task_type === "tts" && t.text_len ? `${t.text_len}字` : "–"}
                    </td>
                    <td className="px-3 py-2.5"><StatusChip status={t.status} /></td>
                    <td className="tnum px-3 py-2.5 text-right font-mono text-xs text-ink-2">{fmt(t.queue_seconds)}s</td>
                    <td className="tnum px-3 py-2.5 text-right font-mono text-xs text-ink-2">{fmt(t.run_seconds)}s</td>
                    <td className="tnum px-3 py-2.5 font-mono text-xs text-ink-2">{fmtTimeFull(t.created_at)}</td>
                    <td className="tnum px-3 py-2.5 font-mono text-xs text-ink-2">{fmtTimeFull(t.finished_at)}</td>
                    <td className="max-w-[220px] truncate px-5 py-2.5 font-mono text-xs text-ink-3" title={t.error ?? ""}>
                      {t.error ? t.error.slice(0, 40) : "–"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-5 py-3">
          <span className="text-xs text-ink-3">第 {data?.page ?? 1} / {pages} 页</span>
          <div className="flex items-center gap-2">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={(data?.page ?? 1) <= 1} className={btnCls}>
              ← 上一页
            </button>
            <button onClick={() => setPage((p) => p + 1)} disabled={(data?.page ?? 1) >= pages} className={btnCls}>
              下一页 →
            </button>
          </div>
        </div>
      </Card>
    </div>
  );
}

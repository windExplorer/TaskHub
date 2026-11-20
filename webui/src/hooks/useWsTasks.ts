import { useEffect, useMemo, useState } from "react";

import type { Task, WsTasksMessage } from "../types";

const MAX_TASKS = 300;

/** WS /ws/tasks：init 全量 + task_update 增量，内存 Map 合并；断线 3s 自动重连。 */
export function useWsTasks() {
  const [map, setMap] = useState<Map<string, Task>>(new Map());
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let closed = false;
    let ws: WebSocket | null = null;
    let timer = 0;

    const connect = () => {
      if (closed) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/tasks`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data as string) as WsTasksMessage;
          if (d.event === "init") {
            const next = new Map<string, Task>();
            for (const t of d.tasks ?? []) next.set(t.task_id, t);
            setMap(next);
          } else if (d.event === "task_update") {
            setMap((prev) => {
              const next = new Map(prev);
              next.set(d.task.task_id, d.task);
              // 防御：超过上限时按创建时间保留最新 300 条（init 可能一次发来全量历史）
              if (next.size > MAX_TASKS) {
                const keep = [...next.values()]
                  .sort((a, b) => b.created_at - a.created_at)
                  .slice(0, MAX_TASKS);
                return new Map(keep.map((t) => [t.task_id, t]));
              }
              return next;
            });
          }
        } catch {
          /* 忽略坏帧 */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = window.setTimeout(connect, 3000);
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, []);

  const list = useMemo(
    () => [...map.values()].sort((a, b) => b.created_at - a.created_at),
    [map],
  );

  return { list, total: map.size, connected };
}

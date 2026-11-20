import { useEffect, useState } from "react";

import type { LogLine } from "../types";

const MAX_LINES = 200;

/** WS /ws/logs：增量日志流，前端最多保留 200 行；断线 3s 自动重连。 */
export function useWsLogs() {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let closed = false;
    let ws: WebSocket | null = null;
    let timer = 0;

    const connect = () => {
      if (closed) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/logs`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data as string) as LogLine;
          setLines((prev) => [...prev.slice(-(MAX_LINES - 1)), d]);
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

  return { lines, connected };
}

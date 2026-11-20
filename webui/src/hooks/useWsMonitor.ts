import { useEffect, useState } from "react";

import type { Monitor, MonitorHistory } from "../types";
import { fmtClock } from "../lib/ui";

const MAX_POINTS = 120;
const EMPTY: MonitorHistory = {
  labels: [],
  cpu: [],
  gpu: [],
  queue: [],
  running: [],
  eff: [],
};

function push(arr: number[], v: number): number[] {
  return [...arr.slice(-(MAX_POINTS - 1)), v];
}

/** WS /ws/monitor：每秒一帧资源快照，同时维护图表用 120 点环形缓冲。断线 3s 自动重连。 */
export function useWsMonitor() {
  const [monitor, setMonitor] = useState<Monitor | null>(null);
  const [connected, setConnected] = useState(false);
  const [history, setHistory] = useState<MonitorHistory>(EMPTY);

  useEffect(() => {
    let closed = false;
    let ws: WebSocket | null = null;
    let timer = 0;

    const connect = () => {
      if (closed) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/monitor`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data as string) as Monitor;
          setMonitor(d);
          setHistory((h) => ({
            labels: [...h.labels.slice(-(MAX_POINTS - 1)), fmtClock(d.ts * 1000)],
            cpu: push(h.cpu, d.cpu_percent),
            gpu: push(h.gpu, d.gpu_load * 100),
            queue: push(h.queue, d.queue_length),
            running: push(h.running, d.running),
            eff: push(h.eff, d.effective_concurrent),
          }));
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

  return { monitor, connected, history };
}

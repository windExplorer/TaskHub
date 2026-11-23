import { useState } from "react";

import { useTheme } from "./hooks/useTheme";
import { useWsMonitor } from "./hooks/useWsMonitor";
import { Header } from "./components/Header";
import type { TabId } from "./components/Header";
import { DashboardPage } from "./components/DashboardPage";
import { TasksPage } from "./components/TasksPage";
import { StatsPage } from "./components/StatsPage";
import { ServicePanel } from "./components/ServicePanel";
import { ApiDocsPage } from "./components/ApiDocsPage";
import { ConfigDialog } from "./components/ConfigDialog";
import { ShutdownDialog } from "./components/ShutdownDialog";
import { ToastContainer, useToasts } from "./components/Toast";

export default function App() {
  const { mode: theme, setMode: setTheme } = useTheme();
  const { monitor, connected, history } = useWsMonitor();
  const { toasts, push, dismiss } = useToasts();
  const [tab, setTab] = useState<TabId>("dash");
  const [configOpen, setConfigOpen] = useState(false);
  const [shutdownOpen, setShutdownOpen] = useState(false);

  return (
    <div className="relative min-h-dvh">
      {/* 背景装饰：网格 + 双色光斑 */}
      <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" aria-hidden>
        <div className="bg-grid absolute inset-x-0 top-0 h-[420px] [mask-image:radial-gradient(ellipse_80%_70%_at_50%_0%,black,transparent)]" />
        <div
          className="absolute -top-24 left-1/2 h-96 w-[42rem] -translate-x-1/2 rounded-full blur-[120px]"
          style={{ background: "var(--glow-1)" }}
        />
        <div
          className="absolute top-40 -right-32 h-80 w-96 rounded-full blur-[120px]"
          style={{ background: "var(--glow-2)" }}
        />
        <div
          className="absolute -bottom-40 -left-32 h-80 w-96 rounded-full blur-[120px]"
          style={{ background: "var(--glow-1)" }}
        />
      </div>

      <Header
        tab={tab}
        onTab={setTab}
        theme={theme}
        onTheme={setTheme}
        monitor={monitor}
        wsConnected={connected}
        onOpenConfig={() => setConfigOpen(true)}
        onShutdown={() => setShutdownOpen(true)}
      />

      <main className="mx-auto max-w-7xl px-4 pb-16 pt-6 sm:px-6 sm:pt-8">
        {tab === "dash" && (
          <DashboardPage monitor={monitor} history={history} push={push} onNavigate={setTab} />
        )}
        {tab === "services" && (
          <ServicePanel push={push} onShutdown={() => setShutdownOpen(true)} />
        )}
        {tab === "tasks" && <TasksPage />}
        {tab === "stats" && <StatsPage />}
        {tab === "docs" && <ApiDocsPage />}
      </main>

      <footer className="border-t border-line/60 py-5 text-center text-xs text-ink-3">
        <p>TaskHub · 本地 LLM 调度中转 — 语音（CosyVoice）/ 绘图（ComfyUI）单飞调度 · 实时监控 · 上游自检</p>
      </footer>

      <ConfigDialog open={configOpen} onClose={() => setConfigOpen(false)} />
      <ShutdownDialog open={shutdownOpen} onClose={() => setShutdownOpen(false)} push={push} />
      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Activity,
  FileText,
  Play,
  RotateCw,
  Send,
  Settings,
  Square,
  X,
} from "lucide-react";

import {
  ApiFailure,
  fetchServiceLogs,
  fetchServices,
  fetchServicesConfig,
  saveServicesConfig,
  serviceAction,
  testAlerts,
} from "../api";
import type { ServiceAction } from "../api";
import type { ServiceInfo, ServicesConfigResp, ServicesResp } from "../types";
import type { ToastKind } from "./Toast";
import { Card } from "./Card";
import { Switch } from "./ui/Switch";

const STATE_META: Record<ServiceInfo["state"], { label: string; cls: string; dot: string }> = {
  running: { label: "运行中", cls: "border-ok/40 bg-ok-bg text-ok", dot: "bg-ok" },
  starting: { label: "启动中", cls: "border-warn/40 bg-warn-bg text-warn", dot: "bg-warn" },
  unhealthy: { label: "健康异常", cls: "border-warn/40 bg-warn-bg text-warn", dot: "bg-warn" },
  stopped: { label: "已停止", cls: "border-line bg-surface-2 text-ink-3", dot: "bg-ink-3" },
  error: { label: "检测失败", cls: "border-err/40 bg-err-bg text-err", dot: "bg-err" },
};

const btn =
  "inline-flex items-center gap-1 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-xs text-ink-2 transition-colors hover:border-accent hover:text-ink disabled:cursor-not-allowed disabled:opacity-40";

function errText(e: unknown): string {
  return e instanceof ApiFailure ? e.detail : String(e);
}

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

function fmtUptime(sec: number | null): string {
  if (sec === null || sec === undefined) return "–";
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}

interface LogView {
  name: string;
  label: string;
  file: string;
  lines: string[];
}

export function ServicePanel({
  push,
}: {
  push: (kind: ToastKind, title: string, detail?: string) => void;
}) {
  const [data, setData] = useState<ServicesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [logView, setLogView] = useState<LogView | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draft, setDraft] = useState<ServicesConfigResp | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const load = useCallback(async (alive: () => boolean) => {
    try {
      const d = await fetchServices();
      if (!alive()) return;
      setData(d);
      setErr(null);
    } catch (e) {
      if (!alive()) return;
      setErr(errText(e));
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    void load(() => mounted);
    const timer = window.setInterval(() => void load(() => mounted), 5000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [load]);

  async function act(s: ServiceInfo, action: ServiceAction) {
    setBusy((prev) => ({ ...prev, [s.name]: true }));
    try {
      const result = await serviceAction(s.name, action);
      const verb = action === "start" ? "启动" : action === "stop" ? "停止" : "重启";
      push("success", `${s.label} 已${verb}`, result.already_running ? "服务本来就在运行" : undefined);
      await load(() => true);
    } catch (e) {
      push("error", `${s.label} 操作失败`, errText(e));
    } finally {
      setBusy((prev) => ({ ...prev, [s.name]: false }));
    }
  }

  async function openLogs(s: ServiceInfo) {
    try {
      const r = await fetchServiceLogs(s.name);
      setLogView({ name: s.name, label: s.label, file: r.file, lines: r.lines ?? [] });
    } catch (e) {
      push("error", "读取日志失败", errText(e));
    }
  }

  async function openSettings() {
    try {
      setDraft(await fetchServicesConfig());
      setSettingsOpen(true);
    } catch (e) {
      push("error", "读取配置失败", errText(e));
    }
  }

  function patch(path: string[], value: unknown) {
    setDraft((prev) => {
      if (!prev) return prev;
      const next = clone(prev);
      let cur: Record<string, any> = next as Record<string, any>;
      for (let i = 0; i < path.length - 1; i++) {
        const key = path[i];
        if (!cur[key] || typeof cur[key] !== "object") cur[key] = {};
        cur = cur[key];
      }
      cur[path[path.length - 1]] = value;
      return next;
    });
  }

  async function saveSettings() {
    if (!draft) return;
    setSaving(true);
    try {
      await saveServicesConfig(draft);
      push("success", "服务面板配置已保存", "开关即时生效；巡检与告警参数在下一轮生效");
      setSettingsOpen(false);
      await load(() => true);
    } catch (e) {
      push("error", "保存失败", errText(e));
    } finally {
      setSaving(false);
    }
  }

  async function sendTest() {
    setTesting(true);
    try {
      const r = await testAlerts();
      const detail = Object.entries(r.results ?? {})
        .map(([k, v]) => `${k}: ${v}`)
        .join(" · ");
      const sent = Object.values(r.results ?? {}).some((v) => v === "sent");
      push(sent ? "success" : "warn", sent ? "测试告警已发送" : "没有成功发送的通道", detail || "未启用任何通道");
    } catch (e) {
      push("error", "测试告警失败", errText(e));
    } finally {
      setTesting(false);
    }
  }

  const alerts = (draft?.alerts ?? {}) as Record<string, any>;
  const channels = (alerts.channels ?? {}) as Record<string, any>;
  const gotify = (channels.gotify ?? {}) as Record<string, any>;
  const email = (channels.email ?? {}) as Record<string, any>;
  const svcCfg = (draft?.services ?? {}) as Record<string, any>;
  const items = Array.isArray(svcCfg.items) ? svcCfg.items : [];

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
            <Activity size={17} className="text-accent" />
            外部服务面板
          </h2>
          <p className="mt-1 text-xs text-ink-3">
            随 TaskHub 开机自启；默认只告警不自动拉起。每 5 秒刷新状态，日志单独落盘不再弹黑框。
          </p>
        </div>
        <div className="flex gap-2">
          <button className={btn} onClick={() => void sendTest()} disabled={testing}>
            <Send size={13} /> 测试告警
          </button>
          <button className={btn} onClick={() => void openSettings()}>
            <Settings size={13} /> 设置
          </button>
        </div>
      </div>

      {err && (
        <div className="rounded-xl border border-err/30 bg-err-bg px-4 py-3 text-sm text-err">
          加载服务状态失败：{err}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {(data?.services ?? []).map((s, i) => {
          const meta = STATE_META[s.state] ?? STATE_META.error;
          const isBusy = !!busy[s.name];
          return (
            <Card key={s.name} delay={i * 0.04} className="flex flex-col gap-3 p-5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-semibold text-ink">{s.label}</h3>
                  <p className="mt-0.5 font-mono text-[11px] text-ink-3">{s.name}</p>
                </div>
                <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${meta.cls}`}>
                  <span className={`mr-1 inline-block size-1.5 rounded-full ${meta.dot}`} />
                  {meta.label}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-y-1.5 text-xs">
                <span className="text-ink-3">健康检查</span>
                <span className={s.health === "ok" ? "text-ok" : s.health === "fail" ? "text-err" : "text-ink-2"}>
                  {s.health === "ok" ? "通过" : s.health === "fail" ? "失败" : "未配置"}
                </span>
                <span className="text-ink-3">PID / 运行</span>
                <span className="font-mono text-ink-2">{s.pid ?? "–"} · {fmtUptime(s.uptime_s)}</span>
                <span className="text-ink-3">CPU / 内存</span>
                <span className="font-mono text-ink-2">{s.cpu_percent}% · {s.rss_mb}MB</span>
                <span className="text-ink-3">自启 / 自愈</span>
                <span className="text-ink-2">{s.autostart ? "开" : "关"} / {s.auto_restart ? "开" : "关"}</span>
              </div>
              {s.health_detail && s.health !== "ok" && (
                <p className="truncate text-[11px] text-ink-3" title={s.health_detail}>{s.health_detail}</p>
              )}

              <div className="mt-auto flex flex-wrap gap-1.5 pt-1">
                <button className={btn} disabled={!s.managed || isBusy || s.state !== "stopped"}
                  onClick={() => void act(s, "start")}>
                  <Play size={12} /> 启动
                </button>
                <button className={btn} disabled={!s.managed || isBusy || !s.alive}
                  onClick={() => void act(s, "stop")}>
                  <Square size={12} /> 停止
                </button>
                <button className={btn} disabled={!s.managed || isBusy}
                  onClick={() => void act(s, "restart")}>
                  <RotateCw size={12} /> 重启
                </button>
                <button className={btn} onClick={() => void openLogs(s)}>
                  <FileText size={12} /> 日志
                </button>
              </div>
            </Card>
          );
        })}
      </div>

      {/* 日志抽屉 */}
      <AnimatePresence>
        {logView && (
          <motion.div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setLogView(null)}>
            <motion.div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl border border-line bg-surface p-5 shadow-card"
              initial={{ scale: 0.96, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.96, y: 12 }}
              onClick={(e) => e.stopPropagation()}>
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-ink">{logView.label} · 日志</h3>
                  <p className="mt-0.5 truncate font-mono text-[11px] text-ink-3" title={logView.file}>{logView.file}</p>
                </div>
                <button className={btn} onClick={() => setLogView(null)} aria-label="关闭日志"><X size={14} /></button>
              </div>
              <pre className="scroll-thin min-h-40 flex-1 overflow-auto rounded-xl border border-line bg-bg p-3 font-mono text-[11px] leading-5 text-ink-2">
                {logView.lines.length ? logView.lines.join("\n") : "（暂无日志）"}
              </pre>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 设置：巡检 / 告警通道 / 各服务开关 */}
      <AnimatePresence>
        {settingsOpen && draft && (
          <motion.div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={() => setSettingsOpen(false)}>
            <motion.div className="scroll-thin max-h-[88vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-line bg-surface p-5 shadow-card"
              initial={{ scale: 0.96, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.96, y: 12 }}
              onClick={(e) => e.stopPropagation()}>
              <div className="mb-4 flex items-center justify-between">
                <h3 className="text-sm font-semibold text-ink">服务面板设置</h3>
                <button className={btn} onClick={() => setSettingsOpen(false)} aria-label="关闭设置"><X size={14} /></button>
              </div>

              <div className="flex flex-col gap-5 text-xs">
                <section>
                  <h4 className="mb-2 text-xs font-semibold text-ink-2">巡检</h4>
                  <div className="grid grid-cols-2 gap-2">
                    {([
                      ["check_interval", "巡检周期(s)"],
                      ["start_grace", "启动宽限(s)"],
                      ["fail_threshold", "失败阈值(次)"],
                      ["max_restart_attempts", "最多自愈(次)"],
                    ] as const).map(([key, label]) => (
                      <label key={key} className="flex items-center justify-between gap-2 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5">
                        <span className="text-ink-2">{label}</span>
                        <input type="number" className="w-20 rounded border border-line bg-bg px-1.5 py-0.5 text-right font-mono text-ink outline-none focus:border-accent"
                          value={svcCfg[key] ?? ""}
                          onChange={(e) => patch(["services", key], Number(e.target.value))} />
                      </label>
                    ))}
                  </div>
                </section>

                <section>
                  <div className="mb-2 flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-ink-2">告警（状态变化去重，冷却期内不重复发）</h4>
                    <Switch checked={alerts.enabled === true}
                      onCheckedChange={(v) => patch(["alerts", "enabled"], v)} ariaLabel="启用告警" />
                  </div>
                  <div className="mb-3 flex flex-wrap gap-3">
                    {([["down", "服务异常"], ["up", "服务恢复"], ["restart_failed", "自愈失败"]] as const).map(([key, label]) => (
                      <label key={key} className="flex items-center gap-1.5 text-ink-2">
                        <Switch checked={(alerts.events?.[key] ?? key === "down") === true}
                          onCheckedChange={(v) => patch(["alerts", "events", key], v)} ariaLabel={label} />
                        {label}
                      </label>
                    ))}
                    <label className="flex items-center gap-2 text-ink-2">
                      冷却(s)
                      <input type="number" className="w-16 rounded border border-line bg-bg px-1.5 py-0.5 text-right font-mono text-ink outline-none focus:border-accent"
                        value={alerts.cooldown_seconds ?? 60}
                        onChange={(e) => patch(["alerts", "cooldown_seconds"], Number(e.target.value))} />
                    </label>
                  </div>

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div className="rounded-xl border border-line bg-surface-2 p-3">
                      <div className="mb-2 flex items-center justify-between">
                        <span className="font-medium text-ink">Gotify</span>
                        <Switch checked={gotify.enabled === true}
                          onCheckedChange={(v) => patch(["alerts", "channels", "gotify", "enabled"], v)} ariaLabel="启用 Gotify" />
                      </div>
                      {([
                        ["url", "服务地址"], ["token", "应用 Token"], ["priority", "优先级"],
                      ] as const).map(([key, label]) => (
                        <label key={key} className="mb-1.5 flex items-center justify-between gap-2">
                          <span className="text-ink-3">{label}</span>
                          <input type={key === "token" ? "password" : "text"}
                            className="w-44 rounded border border-line bg-bg px-1.5 py-0.5 font-mono text-ink outline-none focus:border-accent"
                            value={gotify[key] ?? ""}
                            onChange={(e) => patch(["alerts", "channels", "gotify", key],
                              key === "priority" ? Number(e.target.value) : e.target.value)} />
                        </label>
                      ))}
                    </div>
                    <div className="rounded-xl border border-line bg-surface-2 p-3">
                      <div className="mb-2 flex items-center justify-between">
                        <span className="font-medium text-ink">邮件（SMTP）</span>
                        <Switch checked={email.enabled === true}
                          onCheckedChange={(v) => patch(["alerts", "channels", "email", "enabled"], v)} ariaLabel="启用邮件告警" />
                      </div>
                      {([
                        ["host", "SMTP 主机"], ["port", "端口"], ["user", "账号"],
                        ["password", "密码"], ["from_addr", "发件地址"],
                      ] as const).map(([key, label]) => (
                        <label key={key} className="mb-1.5 flex items-center justify-between gap-2">
                          <span className="text-ink-3">{label}</span>
                          <input type={key === "password" ? "password" : "text"}
                            className="w-44 rounded border border-line bg-bg px-1.5 py-0.5 font-mono text-ink outline-none focus:border-accent"
                            value={email[key] ?? ""}
                            onChange={(e) => patch(["alerts", "channels", "email", key],
                              key === "port" ? Number(e.target.value) : e.target.value)} />
                        </label>
                      ))}
                      <label className="mb-1.5 flex items-center justify-between gap-2">
                        <span className="text-ink-3">收件人（逗号分隔）</span>
                        <input type="text" className="w-44 rounded border border-line bg-bg px-1.5 py-0.5 font-mono text-ink outline-none focus:border-accent"
                          value={(email.to_addrs ?? []).join(", ")}
                          onChange={(e) => patch(["alerts", "channels", "email", "to_addrs"],
                            e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
                      </label>
                      <div className="flex gap-3 pt-1">
                        <label className="flex items-center gap-1.5 text-ink-2">
                          <Switch checked={email.ssl === true}
                            onCheckedChange={(v) => patch(["alerts", "channels", "email", "ssl"], v)} ariaLabel="SSL" /> SSL
                        </label>
                        <label className="flex items-center gap-1.5 text-ink-2">
                          <Switch checked={email.starttls === true}
                            onCheckedChange={(v) => patch(["alerts", "channels", "email", "starttls"], v)} ariaLabel="STARTTLS" /> STARTTLS
                        </label>
                      </div>
                    </div>
                  </div>
                </section>

                <section>
                  <h4 className="mb-2 text-xs font-semibold text-ink-2">服务开关</h4>
                  <div className="overflow-hidden rounded-xl border border-line">
                    {items.map((item: Record<string, any>, idx: number) => (
                      <div key={String(item.name ?? idx)}
                        className="flex items-center justify-between gap-2 border-b border-line px-3 py-2 last:border-b-0">
                        <span className="min-w-0 flex-1 truncate text-ink-2">{String(item.label ?? item.name ?? idx)}</span>
                        {([["enabled", "启用"], ["autostart", "开机自启"], ["auto_restart", "异常自愈"]] as const).map(([key, label]) => (
                          <label key={key} className="flex w-24 items-center justify-end gap-1.5 text-ink-3">
                            {label}
                            <Switch checked={item[key] === true}
                              disabled={key === "enabled" && item.self === true}
                              onCheckedChange={(v) => patch(["services", "items", String(idx), key], v)}
                              ariaLabel={label} />
                          </label>
                        ))}
                      </div>
                    ))}
                  </div>
                </section>
              </div>

              <div className="mt-5 flex justify-end gap-2">
                <button className={btn} onClick={() => setSettingsOpen(false)}>取消</button>
                <button className={`${btn} border-accent/50 text-accent`} disabled={saving}
                  onClick={() => void saveSettings()}>{saving ? "保存中…" : "保存配置"}</button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

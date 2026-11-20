import { useState } from "react";
import {
  Activity,
  Flame,
  Gauge,
  HardDrive,
  Image as ImageIcon,
  RefreshCw,
  Server,
  ShieldCheck,
  Thermometer,
  Volume2,
  type LucideIcon,
} from "lucide-react";

import { nsfwRelease, nsfwWarm } from "../api";
import { Tooltip } from "./ui/Tooltip";
import { fmt, fmtClock, fmtTime, fmtUptime } from "../lib/ui";
import { useDevice } from "../hooks/useDevice";
import type {
  ComfyUpstream,
  CosyUpstream,
  DeviceHost,
  NsfwUpstream,
} from "../types";

/** 设备面板（GET /device 每 10s 自动刷新）：本机硬件小卡片 + 三个同级业务服务卡。 */

function Meter({ pct, color }: { pct: number | null | undefined; color: string }) {
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  return (
    <div className="mt-1 h-1 overflow-hidden rounded bg-surface-2">
      <div
        className="h-full rounded transition-[width] duration-500"
        style={{ width: `${p}%`, background: color }}
      />
    </div>
  );
}

function Kv({ label, value, title }: { label: string; value: React.ReactNode; title?: string }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-line py-1 last:border-b-0">
      <span className="shrink-0 text-ink-3">{label}</span>
      <span className="truncate text-right font-mono text-[11px] text-ink-2" title={title ?? String(value)}>
        {value === null || value === undefined || value === "" ? "–" : value}
      </span>
    </div>
  );
}

/** 本机硬件小卡片：图标 + 标签 + 主值 + 副行 + 可选水位条 */
function MiniCard({
  icon: Icon,
  label,
  value,
  sub,
  meter,
  title,
  wrap = false,
}: {
  icon: LucideIcon;
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  meter?: { pct: number; color: string };
  title?: string;
  wrap?: boolean;
}) {
  return (
    <div className="rounded-xl border border-line/70 bg-surface-2/60 p-3">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-ink-3">
        <Icon size={13} className="shrink-0" />
        <span className="truncate" title={label}>
          {label}
        </span>
      </div>
      <div
        className={`tnum font-mono text-sm text-ink ${wrap ? "break-words leading-snug" : "truncate"}`}
        title={title ?? (typeof value === "string" ? value : undefined)}
      >
        {value}
      </div>
      {sub && (
        <div className={`mt-0.5 text-[10px] text-ink-3 ${wrap ? "break-words leading-snug" : "truncate"}`}>
          {sub}
        </div>
      )}
      {meter && <Meter pct={meter.pct} color={meter.color} />}
    </div>
  );
}

/** 业务服务卡：三张同级，图标色块 + 名称 + 状态点 + 详情 */
function ServiceCard({
  icon: Icon,
  name,
  caption,
  state,
  ok,
  accent,
  children,
}: {
  icon: LucideIcon;
  name: string;
  caption?: string;
  state: string;
  /** null = 未启用（灰点） */
  ok: boolean | null;
  accent: string;
  children: React.ReactNode;
}) {
  const dot = ok === null ? "var(--mute)" : ok ? "var(--ok)" : "var(--err)";
  return (
    <div className="rounded-xl border border-line/70 bg-surface-2/60 p-3">
      <div className="mb-2 flex items-center gap-2 border-b border-dashed border-line pb-2">
        <span
          className="flex size-7 shrink-0 items-center justify-center rounded-lg"
          style={{ background: `color-mix(in oklch, ${accent} 14%, transparent)`, color: accent }}
        >
          <Icon size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-semibold text-ink-2">{name}</div>
          {caption && <div className="truncate text-[10px] text-ink-3">{caption}</div>}
        </div>
        <span className="flex shrink-0 items-center gap-1 text-[10px]" style={{ color: dot }}>
          <span className="size-1.5 rounded-full" style={{ background: dot }} />
          {state}
        </span>
      </div>
      <div className="text-[11px]">{children}</div>
    </div>
  );
}

interface OsInfo {
  caption?: string;
  name?: string;
  version?: string;
  display_version?: string;
  build?: string;
  arch?: string;
  install_time?: number;
}

/** 本机硬件小卡片（只放顶部指标卡没有的信息，避免与顶部 CPU/内存/GPU/显存重复） */
function hostCards(h: DeviceHost): React.ReactNode[] {
  const cpu = h.cpu ?? {};
  const disk = h.disk ?? {};
  const proc = h.process ?? {};
  const gpus = h.gpus ?? [];
  const os: OsInfo =
    h.os && typeof h.os === "object" ? (h.os as OsInfo) : { caption: h.os as string | undefined };
  // 后端 platform.release() 在 Win11 上返回 10，旧进程未重启时 caption 仍是 "Windows 10"。
  // 按内部版本号兜底纠正（22000 = Win11 首个版本），保证不重启后端也显示正确。
  const buildNum = Number(os.build);
  const osCaption = (os.caption ?? os.name ?? "–").replace(
    /Windows\s*10/,
    buildNum >= 22000 ? "Windows 11" : "Windows 10",
  );
  const osTitle = [
    os.version ? `版本 ${os.version}` : null,
    os.display_version ? `功能更新 ${os.display_version}` : null,
    os.build ? `内部版本 ${os.build}` : null,
    os.arch ?? null,
    os.install_time ? `安装于 ${fmtTime(os.install_time)}` : null,
    `主机名 ${h.hostname ?? "–"}`,
  ]
    .filter(Boolean)
    .join(" · ");
  const osSub = [
    h.hostname,
    os.display_version ? `功能更新 ${os.display_version}` : null,
    os.build ? `内部版本 ${os.build}` : null,
    `运行 ${fmtUptime(h.service_uptime_s)}`,
  ]
    .filter(Boolean)
    .join(" · ");

  const th = h.thermal ?? {};
  const cores = th.cpu_cores_c ?? {};
  const coreNames = Object.keys(cores);
  const acpi = Object.entries(th.acpi_zones_c ?? {})
    .map(([k, v]) => `${k} ${v}°C`)
    .join(" / ");
  let cpuTemp: string | null = null;
  if (th.cpu_package_c !== null && th.cpu_package_c !== undefined) {
    cpuTemp = `${fmt(th.cpu_package_c, 0)}°C`;
  }
  if (!cpuTemp && coreNames.length) {
    cpuTemp = `${fmt(Math.max(...coreNames.map((k) => cores[k])), 0)}°C`;
  }
  if (!cpuTemp) cpuTemp = acpi ? `不可用（ACPI 热区 ${acpi}）` : "不可用";
  const gpuCompute = gpus.length
    ? gpus.map((g) => `GPU${g.index} ${fmt(g.util_percent, 0)}%`).join(" · ")
    : "–";
  const gpuThermal = gpus
    .map((g) => {
      const bits = [
        g.temperature_c !== undefined && g.temperature_c !== null ? `${fmt(g.temperature_c, 0)}°C` : null,
        g.power_w !== undefined && g.power_w !== null ? `${fmt(g.power_w, 0)}W` : null,
        g.fan_percent !== undefined && g.fan_percent !== null ? `风扇 ${fmt(g.fan_percent, 0)}%` : null,
      ]
        .filter(Boolean)
        .join(" · ");
      return bits ? `GPU${g.index} ${bits}` : null;
    })
    .filter(Boolean)
    .join("；");
  const cpuTempTitle = [
    th.cpu_package_c !== null && th.cpu_package_c !== undefined
      ? `CPU Package ${fmt(th.cpu_package_c, 0)}°C`
      : null,
    ...coreNames.map((k) => `${k} ${cores[k]}°C`),
    ...Object.entries(th.other_c ?? {}).map(([k, v]) => `${k} ${v}°C`),
    acpi ? `ACPI 热区：${acpi}` : null,
    th.source ? `来源：${th.source}` : null,
    th.note ?? null,
    gpuThermal ? `显卡：${gpuThermal}` : null,
  ]
    .filter(Boolean)
    .join("\n");
  const cpuDetail = `${cpu.model ?? ""}（${cpu.cores_physical ?? "?"}C/${cpu.cores_logical ?? "?"}T${cpu.current_mhz ? ` · ${cpu.current_mhz}MHz` : ""}）`;

  const cards: React.ReactNode[] = [
    <MiniCard
      key="sys"
      icon={Server}
      label="系统"
      value={osCaption}
      sub={osSub}
      title={osTitle}
      wrap
    />,
    <MiniCard
      key="cpu-model"
      icon={Activity}
      label="CPU 型号"
      value={cpu.model ?? "–"}
      sub={`${cpu.cores_physical ?? "?"}C/${cpu.cores_logical ?? "?"}T${cpu.current_mhz ? ` · ${cpu.current_mhz}MHz` : ""}`}
      title={cpuDetail}
    />,
    <MiniCard
      key="disk"
      icon={HardDrive}
      label={`磁盘(${disk.path ?? "."})`}
      value={`剩 ${fmt(disk.free_gb, 1)} GB`}
      sub={`共 ${fmt(disk.total_gb, 1)} GB`}
      meter={{ pct: disk.percent ?? 0, color: (disk.percent ?? 0) > 90 ? "var(--err)" : "var(--mute)" }}
    />,
    <MiniCard
      key="temp"
      icon={Thermometer}
      label="CPU 温度"
      value={cpuTemp}
      sub={th.source ?? undefined}
      title={cpuTempTitle.split("\n").filter((x) => !x.startsWith("显卡：")).join("\n")}
    />,
    <MiniCard
      key="gpu-compute"
      icon={Gauge}
      label="显卡算力"
      value={gpuCompute}
      sub={gpuThermal || undefined}
      title={gpuThermal || gpuCompute}
      wrap
    />,
    <MiniCard
      key="proc"
      icon={Activity}
      label="本进程"
      value={proc.pid ? `pid ${proc.pid} · ${fmt(proc.rss_gb, 2)} GB` : "–"}
      sub={proc.pid ? `${proc.threads ?? "–"} 线程 · ${fmtUptime(proc.uptime_s)}` : undefined}
    />,
  ];
  return cards;
}

function ComfyService({ c }: { c: ComfyUpstream }) {
  const q = c.queue ?? {};
  const sys = c.system ?? {};
  const st = c.progress_stream;
  return (
    <ServiceCard
      icon={ImageIcon}
      name="ComfyUI"
      caption={c.base_url}
      state={c.reachable ? "在线" : "不可访问"}
      ok={!!c.reachable}
      accent="var(--accent-2)"
    >
      <Kv
        label="队列"
        value={`执行中 ${(q.running ?? []).length} · 排队 ${(q.pending ?? []).length}`}
        title={`queue_remaining=${q.queue_remaining ?? "?"}`}
      />
      {st && (
        <Kv
          label="进度订阅"
          value={
            st.connected
              ? `已连接（事件 ${st.last_event_ago === null || st.last_event_ago === undefined ? "–" : `${st.last_event_ago}s`} 前）`
              : "未连接"
          }
          title={`${st.last_event_type ?? ""}｜errors=${st.errors ?? 0}`}
        />
      )}
      {sys.comfyui_version ? (
        <>
          <Kv label="版本" value={`ComfyUI ${sys.comfyui_version}`} />
          <Kv label="运行环境" value={`Python ${sys.python ?? "?"} · torch ${sys.pytorch ?? "?"}`} />
          <Kv
            label="上游系统"
            value={`${sys.os ?? "?"} · 内存可用 ${fmt(sys.ram_free_gb, 1)}/${fmt(sys.ram_total_gb, 1)}GB`}
          />
        </>
      ) : (
        <div className="py-1 text-ink-3">/system_stats 不可用（上游未响应或未开放）</div>
      )}
      {(c.devices ?? []).map((d) => {
        const used = (d.vram_total_gb ?? 0) - (d.vram_free_gb ?? 0);
        const pct = d.vram_total_gb ? (used / d.vram_total_gb) * 100 : 0;
        return (
          <Kv
            key={d.name}
            label={`显存 ${d.name ?? "device"}`}
            value={`${fmt(d.vram_free_gb, 2)} / ${fmt(d.vram_total_gb, 1)} GB（${fmt(pct, 0)}%）`}
            title={`torch 已分配 ${fmt(d.torch_vram_total_gb, 2)} GB（缓存 ${fmt(d.torch_vram_free_gb, 2)}GB）`}
          />
        );
      })}
    </ServiceCard>
  );
}

function TtsService({ c }: { c: CosyUpstream }) {
  return (
    <ServiceCard
      icon={Volume2}
      name="TTS 服务"
      caption={c.base_url}
      state={!c.reachable ? "不可访问" : c.model_loaded ? "就绪" : "模型未加载"}
      ok={!!c.reachable && !!c.model_loaded}
      accent="var(--info)"
    >
      <Kv
        label="模型状态"
        value={c.model_loaded ? "已加载" : "未就绪"}
        title={c.model_loaded ? "" : "语音任务会返回 503"}
      />
      <Kv label="采样率" value={`${c.sample_rate ?? "–"} Hz`} />
      <Kv label="参考音色" value={`${c.voices ?? "–"} 个`} />
      <Kv label="参考目录" value={c.voices_dir ?? "（未配置）"} />
      <Kv label="探测时间" value={c.last_probe_ts ? fmtTime(c.last_probe_ts) : "–"} />
      {c.info && Object.keys(c.info).length > 0 && (
        <Kv label="上游自述" value={JSON.stringify(c.info)} title={JSON.stringify(c.info, null, 2)} />
      )}
    </ServiceCard>
  );
}

function NsfwService({ n, onChanged }: { n: NsfwUpstream; onChanged: () => void }) {
  const h = n.health;
  const state = !n.enabled ? "未启用" : n.reachable ? "在线" : "不可访问";
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const loaded = !!(h?.main_loaded && h?.aux_loaded);

  const act = async (kind: "warm" | "release") => {
    setBusy(true);
    setMsg("");
    try {
      const r = kind === "warm" ? await nsfwWarm() : await nsfwRelease();
      setMsg(r.ok ? (kind === "warm" ? "预热完成" : "已卸载") : "操作失败");
      onChanged();
    } catch {
      setMsg("操作失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ServiceCard
      icon={ShieldCheck}
      name="NSFW 检测"
      caption={n.base_url}
      state={state}
      ok={n.enabled ? !!n.reachable : null}
      accent="var(--warn)"
    >
      <Kv label="送检模式" value={n.mode ?? "–"} title="label=随 history 返回结果；block=违规图直接拦截" />
      <Kv label="送检前预热" value={n.prewarm ? "开" : "关"} title="送检前自动预热模型，消除卸载后的冷启动" />
      {n.enabled && n.reachable && h ? (
        <>
          <Kv label="服务版本" value={h.version ?? "–"} />
          <Kv label="检测模型" value={h.model ?? "–"} />
          <Kv
            label="模型就绪"
            value={`主 ${h.main_ready ? "是" : "否"} · 辅 ${h.aux_ready ? "是" : "否"}`}
            title={`已加载：主 ${h.main_loaded ? "是" : "否"} · 辅 ${h.aux_loaded ? "是" : "否"}`}
          />
          <Kv label="推理设备" value={h.device ?? "–"} />
          <Kv label="空闲" value={h.idle_sec === undefined ? "–" : `${h.idle_sec}s`} />
        </>
      ) : (
        <div className="py-1 text-ink-3">
          {n.enabled ? "未收到 /healthz 响应（送检会自动降级，不阻断出图）" : "配置未启用，零开销"}
        </div>
      )}
      {n.enabled && n.reachable && (
        <div className="mt-2 flex items-center gap-2 border-t border-line pt-2">
          <Tooltip content={loaded ? "模型已加载" : "手动加载/预热模型"}>
            <button
              type="button"
              disabled={busy || loaded}
              onClick={() => void act("warm")}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-ink-2 transition hover:bg-surface disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Flame size={12} />
              预热
            </button>
          </Tooltip>
          <Tooltip content={loaded ? "卸载模型释放显存" : "模型未加载"}>
            <button
              type="button"
              disabled={busy || !loaded}
              onClick={() => void act("release")}
              className="flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-ink-2 transition hover:bg-surface disabled:cursor-not-allowed disabled:opacity-40"
            >
              <RefreshCw size={12} />
              卸载
            </button>
          </Tooltip>
          {busy && <span className="text-[10px] text-ink-3">处理中…</span>}
          {!busy && msg && <span className="text-[10px] text-ink-3">{msg}</span>}
        </div>
      )}
    </ServiceCard>
  );
}

export function DevicePanel() {
  const { device, error, updatedAt, refresh } = useDevice();
  const sc = device?.scheduler ?? {};
  const up = device?.upstreams ?? {};

  return (
    <div>
      <div className="flex flex-col gap-2 border-b border-line px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium text-ink-2">设备状态</h2>
          <span className="text-[11px] text-ink-3">本机硬件 + 业务服务（每 10s 自动刷新）</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-ink-3">
            {updatedAt
              ? `更新于 ${fmtClock(updatedAt)}` +
                (sc.queue_length !== undefined
                  ? ` · 调度：队列 ${sc.queue_length ?? 0} / 运行 ${sc.running ?? 0}（并发 ${sc.effective_concurrent ?? 0}）` +
                    (sc.throttle_reason && sc.throttle_reason !== "normal"
                      ? ` · 降级：${sc.throttle_reason}`
                      : "")
                  : "")
              : "加载中…"}
          </span>
          <Tooltip content="立即刷新">
            <button
              onClick={() => void refresh()}
              className="rounded-full border border-line p-1.5 text-ink-3 transition-colors hover:border-line-strong hover:text-ink"
            >
              <RefreshCw size={13} />
            </button>
          </Tooltip>
        </div>
      </div>

      <div className="space-y-5 p-5">
        <section>
          <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
            本机硬件
          </h3>
          {error ? (
            <div className="text-xs text-err">加载设备状态失败：{error}</div>
          ) : device ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
              {hostCards(device.host ?? {})}
            </div>
          ) : (
            <div className="text-xs text-ink-3">加载中…</div>
          )}
        </section>

        <section>
          <h3 className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-ink-3">
            业务服务
          </h3>
          {error ? (
            <div className="text-xs text-err">加载设备状态失败：{error}</div>
          ) : device ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <ComfyService c={up.comfyui ?? {}} />
              <TtsService c={up.cosyvoice ?? {}} />
              <NsfwService n={up.nsfw ?? {}} onChanged={() => void refresh()} />
            </div>
          ) : (
            <div className="text-xs text-ink-3">加载中…</div>
          )}
        </section>
      </div>
    </div>
  );
}

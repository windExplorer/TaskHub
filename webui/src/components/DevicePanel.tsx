import { RefreshCw } from "lucide-react";

import { fmt, fmtClock, fmtTime, fmtUptime } from "../lib/ui";
import { useDevice } from "../hooks/useDevice";
import type { ComfyUpstream, CosyUpstream, DeviceHost, GpuInfo } from "../types";

/** 设备面板（GET /device 每 10s 自动刷新）：本机 / ComfyUI / CosyVoice 三 pane。 */

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
    <div className="flex items-center justify-between gap-2 border-b border-line py-1">
      <span className="shrink-0 text-ink-3">{label}</span>
      <span className="truncate text-right font-mono text-[11px] text-ink-2" title={title ?? String(value)}>
        {value === null || value === undefined || value === "" ? "–" : value}
      </span>
    </div>
  );
}

function Dot({ ok, text }: { ok: boolean; text: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[11px] ${ok ? "text-ok" : "text-err"}`}
    >
      <span className={`size-1.5 rounded-full ${ok ? "bg-ok" : "bg-err"}`} />
      {text}
    </span>
  );
}

function PaneTitle({ text, right }: { text: string; right?: string }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <span className="text-xs font-semibold uppercase tracking-wider text-accent">{text}</span>
      <span className="text-[11px] text-ink-3">{right ?? ""}</span>
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

function gpuBlock(g: GpuInfo) {
  const util = Number(g.util_percent) || 0;
  const memPct = Number(g.mem_percent) || 0;
  const utilCol = util > 80 ? "var(--err)" : "var(--accent-2)";
  const memCol = memPct > 90 ? "var(--err)" : "var(--info)";
  const ext = [
    g.temperature_c !== undefined && g.temperature_c !== null ? `温度 ${g.temperature_c}°C` : null,
    g.power_w !== undefined && g.power_w !== null
      ? `功耗 ${g.power_w}${g.power_limit_w ? `/${g.power_limit_w}` : ""}W`
      : null,
    g.fan_percent !== undefined && g.fan_percent !== null ? `风扇 ${g.fan_percent}%` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div key={g.index} className="mt-2 border-t border-dashed border-line pt-2">
      <div className="truncate text-[11px] text-ink-2" title={g.name}>
        #{g.index} {g.name || "GPU"}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-ink-3">
        <span>算力</span>
        <span className="tnum font-mono">{fmt(util, 0)}%</span>
      </div>
      <Meter pct={util} color={utilCol} />
      <div className="mt-1 flex justify-between text-[11px] text-ink-3">
        <span>显存</span>
        <span className="tnum font-mono">
          {fmt(g.mem_used_gb, 1)} / {fmt(g.mem_total_gb, 1)} GB
        </span>
      </div>
      <Meter pct={memPct} color={memCol} />
      {ext && (
        <div className="tnum mt-1 font-mono text-[11px] text-ink-3">
          {ext}
          {g.clock_mhz ? ` · ${g.clock_mhz}MHz` : ""}
        </div>
      )}
    </div>
  );
}

function hostPane(h: DeviceHost) {
  const cpu = h.cpu ?? {};
  const ram = h.ram ?? {};
  const disk = h.disk ?? {};
  const proc = h.process ?? {};
  const gpus = h.gpus ?? [];
  // 系统版本详情（老响应可能是字符串，兼容一下）
  const os: OsInfo =
    h.os && typeof h.os === "object" ? (h.os as OsInfo) : { caption: h.os as string | undefined };
  const osTitle = [
    os.version ? `版本 ${os.version}` : null,
    os.display_version ? `功能更新 ${os.display_version}` : null,
    os.build ? `内部版本 ${os.build}` : null,
    os.arch ?? null,
    os.install_time ? `安装于 ${fmtTime(os.install_time)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  // CPU 温度：Windows 上没有免驱来源时按 封装 → 核心最大值 → ACPI 热区 逐级兜底
  const th = h.thermal ?? {};
  const fans = h.fans ?? {};
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
  const cpuTempTitle = [
    th.cpu_package_c !== null && th.cpu_package_c !== undefined
      ? `CPU Package ${fmt(th.cpu_package_c, 0)}°C`
      : null,
    ...coreNames.map((k) => `${k} ${cores[k]}°C`),
    ...Object.entries(th.other_c ?? {}).map(([k, v]) => `${k} ${v}°C`),
    acpi ? `ACPI 热区：${acpi}` : null,
    th.source ? `来源：${th.source}` : null,
    th.note ?? null,
  ]
    .filter(Boolean)
    .join("\n");
  const sysFans =
    (fans.system ?? []).map((f) => `${f.name} ${f.rpm}RPM`).join(" · ") || "不可用";

  return (
    <div className="rounded-xl bg-surface-2/70 p-3">
      <PaneTitle text="本机" right={h.hostname ?? ""} />
      <Kv label="系统" value={os.caption ?? os.name ?? "–"} title={osTitle} />
      <Kv label="服务运行" value={fmtUptime(h.service_uptime_s)} title={`主机已运行 ${fmtUptime(h.host_uptime_s)}`} />
      <div className="mt-2 border-t border-dashed border-line pt-2">
        <Kv
          label="CPU"
          value={`${fmt(cpu.percent, 0)}%`}
          title={`${cpu.model ?? ""}（${cpu.cores_physical ?? "?"}C/${cpu.cores_logical ?? "?"}T${cpu.current_mhz ? ` · ${cpu.current_mhz}MHz` : ""}）`}
        />
        <Meter pct={cpu.percent} color={(cpu.percent ?? 0) > 85 ? "var(--err)" : "var(--info)"} />
        <div className="truncate text-[10px] text-ink-3">{cpu.model ?? ""}</div>
        <div className="mt-1">
          <Kv label="CPU 温度" value={cpuTemp} title={cpuTempTitle} />
        </div>
        <Kv label="内存" value={`${fmt(ram.used_gb, 1)} / ${fmt(ram.total_gb, 1)} GB`} />
        <Meter pct={ram.percent} color={(ram.percent ?? 0) > 85 ? "var(--err)" : "var(--ok)"} />
        {disk.total_gb ? (
          <>
            <Kv
              label={`磁盘(${disk.path ?? "."})`}
              value={`剩 ${fmt(disk.free_gb, 1)} / ${fmt(disk.total_gb, 1)} GB`}
            />
            <Meter pct={disk.percent} color={(disk.percent ?? 0) > 90 ? "var(--err)" : "var(--mute)"} />
          </>
        ) : null}
        <Kv label="机箱风扇" value={sysFans} title={fans.note} />
        <Kv
          label="本进程"
          value={
            proc.pid
              ? `pid ${proc.pid} · ${fmt(proc.rss_gb, 2)}GB · ${proc.threads ?? "–"} 线程`
              : "–"
          }
        />
      </div>
      {h.nvidia_ok === false && (
        <div className="mt-2 text-[11px] text-warn">未检测到 NVIDIA GPU（NVML 不可用）</div>
      )}
      {gpus.map(gpuBlock)}
      {gpus.length > 0 && (
        <div className="mt-1 text-[10px] text-ink-3">驱动 {h.driver_version ?? "?"}</div>
      )}
    </div>
  );
}

function comfyPane(c: ComfyUpstream) {
  const q = c.queue ?? {};
  const sys = c.system ?? {};
  const st = c.progress_stream;
  const devs = (c.devices ?? []).map((d) => {
    const used = (d.vram_total_gb ?? 0) - (d.vram_free_gb ?? 0);
    const pct = d.vram_total_gb ? (used / d.vram_total_gb) * 100 : 0;
    return (
      <div key={d.name} className="mt-2 border-t border-dashed border-line pt-2">
        <div className="truncate text-[11px] text-ink-2" title={d.name}>
          {d.name ?? "device"}
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-ink-3">
          <span>显存可用</span>
          <span className="tnum font-mono">
            {fmt(d.vram_free_gb, 2)} / {fmt(d.vram_total_gb, 1)} GB
          </span>
        </div>
        <Meter pct={pct} color={pct > 90 ? "var(--err)" : "var(--info)"} />
        <Kv
          label="torch 已分配"
          value={`${fmt(d.torch_vram_total_gb, 2)} GB（缓存 ${fmt(d.torch_vram_free_gb, 2)}GB）`}
        />
      </div>
    );
  });
  return (
    <div className="rounded-xl bg-surface-2/70 p-3">
      <PaneTitle text="ComfyUI" />
      <div className="mb-1">
        <Dot ok={!!c.reachable} text={c.reachable ? "可访问" : "不可访问"} />
      </div>
      <Kv label="地址" value={c.base_url} />
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
        <div className="mt-2 border-t border-dashed border-line pt-2">
          <Kv label="版本" value={`ComfyUI ${sys.comfyui_version}`} />
          <Kv label="运行环境" value={`Python ${sys.python ?? "?"} · torch ${sys.pytorch ?? "?"}`} />
          <Kv
            label="上游系统"
            value={`${sys.os ?? "?"} · 内存可用 ${fmt(sys.ram_free_gb, 1)}/${fmt(sys.ram_total_gb, 1)}GB`}
          />
        </div>
      ) : (
        <div className="mt-2 text-[11px] text-ink-3">
          /system_stats 不可用（上游未响应或未开放）
        </div>
      )}
      {devs}
    </div>
  );
}

function cosyPane(c: CosyUpstream) {
  const info = c.info;
  return (
    <div className="rounded-xl bg-surface-2/70 p-3">
      <PaneTitle text="CosyVoice" />
      <div className="mb-1">
        <Dot ok={!!c.reachable} text={c.reachable ? "可访问" : "不可访问"} />
      </div>
      <Kv label="地址" value={c.base_url} />
      <Kv
        label="模型状态"
        value={c.model_loaded ? "已加载" : "未就绪"}
        title={c.model_loaded ? "" : "语音任务会返回 503"}
      />
      <Kv label="采样率" value={`${c.sample_rate ?? "–"} Hz`} />
      <Kv label="参考音色" value={`${c.voices ?? "–"} 个`} />
      <Kv label="参考目录" value={c.voices_dir ?? "（未配置）"} />
      <Kv label="探测时间" value={c.last_probe_ts ? fmtTime(c.last_probe_ts) : "–"} />
      {info && Object.keys(info).length > 0 && (
        <Kv label="上游自述" value={JSON.stringify(info)} title={JSON.stringify(info, null, 2)} />
      )}
    </div>
  );
}

export function DevicePanel() {
  const { device, error, updatedAt, refresh } = useDevice();
  const sc = device?.scheduler ?? {};

  return (
    <div>
      <div className="flex flex-col gap-2 border-b border-line px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium text-ink-2">设备状态</h2>
          <span className="text-[11px] text-ink-3">本机 + 上游后端（每 10s 自动刷新）</span>
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
          <button
            onClick={() => void refresh()}
            title="立即刷新"
            className="rounded-full border border-line p-1.5 text-ink-3 transition-colors hover:border-line-strong hover:text-ink"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 p-5 lg:grid-cols-3">
        {error ? (
          <div className="text-xs text-err">加载设备状态失败：{error}</div>
        ) : device ? (
          <>
            {hostPane(device.host ?? {})}
            {comfyPane(device.upstreams?.comfyui ?? {})}
            {cosyPane(device.upstreams?.cosyvoice ?? {})}
          </>
        ) : (
          <div className="text-xs text-ink-3">加载中…</div>
        )}
      </div>
    </div>
  );
}

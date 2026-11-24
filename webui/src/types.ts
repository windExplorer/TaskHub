/** 后端数据结构（对齐 scheduler.Task.to_dict 与各 API 响应字段） */

export type TaskType = "tts" | "comfyui" | "nsfw";

export type TaskStatus =
  | "queued"
  | "waiting"
  | "running"
  | "done"
  | "failed"
  | "timeout"
  | "cancelled";

export interface TaskProgress {
  nodes_done?: number;
  nodes_total?: number;
  node?: string;
  step?: number;
  step_total?: number;
  state?: string;
}

export interface TaskAnomaly {
  level: "info" | "warn" | "error";
  code: string;
  msg: string;
  ts: number;
}

/** NSFW 送检结论（detect/simple 聚合后的最严一张；available=false 表示检测未完成） */
export interface TaskNsfw {
  available?: boolean;
  level?: "nsfw" | "review" | "safe" | (string & {});
  action?: "block" | "review" | "allow" | (string & {});
  score?: number;
  error?: string;
  filename?: string;
  images_checked?: number;
  record_id?: string | number;
}

export interface Task {
  task_id: string;
  task_type: TaskType;
  priority: number;
  status: TaskStatus;
  status_code: number;
  error: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  queue_seconds: number;
  run_seconds: number;
  resource_weight: number;
  estimated_duration: number;
  text_len: number;
  progress: TaskProgress;
  anomaly: TaskAnomaly | null;
  nsfw: TaskNsfw | null;
}

// ---------- 自检 ----------

export type SelfCheckLevel = "ok" | "info" | "warn" | "error";

export interface SelfCheckItem {
  key: string;
  level: SelfCheckLevel;
  code: string;
  msg: string;
  task_id: string | null;
  ts: number;
}

export interface ProgressStream {
  connected: boolean;
  connected_seconds?: number;
  last_event_ts?: number | null;
  last_event_ago?: number | null;
  last_event_type?: string;
  queue_remaining?: number | null;
  errors?: number;
}

export interface SelfCheck {
  level: SelfCheckLevel;
  count: number;
  items: SelfCheckItem[];
  last_run?: number;
  cycles?: number;
  probe?: {
    reachable: boolean;
    running: string[];
    pending: string[];
    vram_free_gb?: number;
    vram_total_gb?: number;
  };
  progress_stream?: ProgressStream;
}

// ---------- 监控 ----------

export interface Monitor {
  cpu_percent: number;
  ram_available_gb: number;
  ram_used_gb?: number;
  ram_total_gb?: number;
  gpu_load: number;
  gpu_free_gb?: number;
  gpu_total_gb?: number;
  gpu_threshold: number;
  ts: number;
  queue_length: number;
  running: number;
  max_concurrent: number;
  effective_concurrent: number;
  throttle_reason?: string;
  self_check?: SelfCheck | null;
}

/** 监控图表 120 点环形缓冲 */
export interface MonitorHistory {
  labels: string[];
  cpu: number[];
  gpu: number[];
  queue: number[];
  running: number[];
  eff: number[];
}

// ---------- 统计 ----------

export interface StatsBrief {
  window_hours: number;
  total: number;
  done: number;
  failed: number;
  success_rate: number;
  by_type: Record<string, number>;
  avg_run_seconds: number;
}

export interface SeriesPoint {
  ts: number;
  total: number;
  done: number;
  failed: number;
  avg_run: number;
}

export interface StatsDetail {
  hours: number;
  bucket: "hour" | "day";
  summary: {
    total: number;
    done: number;
    failed: number;
    success_rate: number;
    avg_run_seconds: number;
  };
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  series: SeriesPoint[];
}

export interface TasksPageData {
  total: number;
  page: number;
  page_size: number;
  pages: number;
  tasks: Task[];
}

// ---------- 设备状态（GET /device） ----------

export interface GpuInfo {
  index: number;
  name?: string;
  util_percent?: number | null;
  mem_total_gb?: number | null;
  mem_used_gb?: number | null;
  mem_free_gb?: number | null;
  mem_percent?: number | null;
  temperature_c?: number | null;
  power_w?: number | null;
  power_limit_w?: number | null;
  fan_percent?: number | null;
  clock_mhz?: number | null;
  mem_clock_mhz?: number | null;
}

export interface DeviceHost {
  hostname?: string;
  os?: Record<string, unknown> | string;
  host_uptime_s?: number;
  service_uptime_s?: number;
  cpu?: {
    model?: string;
    percent?: number;
    cores_physical?: number;
    cores_logical?: number;
    current_mhz?: number;
    max_mhz?: number;
  };
  ram?: { total_gb?: number; used_gb?: number; available_gb?: number; percent?: number };
  disk?: { path?: string; total_gb?: number; used_gb?: number; free_gb?: number; percent?: number };
  process?: { pid?: number; uptime_s?: number; threads?: number; rss_gb?: number; cpu_percent?: number };
  nvidia_ok?: boolean;
  driver_version?: string;
  gpus?: GpuInfo[];
  thermal?: {
    cpu_package_c?: number | null;
    cpu_cores_c?: Record<string, number>;
    other_c?: Record<string, number>;
    acpi_zones_c?: Record<string, number>;
    source?: string;
    note?: string;
  };
  fans?: {
    gpu?: Array<{ index: number; percent: number }>;
    system?: Array<{ name: string; rpm: number }>;
    note?: string;
  };
}

export interface ComfyDevice {
  name?: string;
  type?: string;
  vram_total_gb?: number;
  vram_free_gb?: number;
  torch_vram_total_gb?: number;
  torch_vram_free_gb?: number;
}

export interface ComfyUpstream {
  base_url?: string;
  reachable?: boolean;
  system_stats_ok?: boolean;
  client_id?: string;
  queue?: { running?: string[]; pending?: string[]; queue_remaining?: number | null };
  progress_stream?: ProgressStream;
  system?: {
    comfyui_version?: string;
    os?: string;
    python?: string;
    pytorch?: string;
    ram_total_gb?: number;
    ram_free_gb?: number;
    argv?: string[];
  };
  devices?: ComfyDevice[];
  raw_system_stats?: unknown;
}

export interface CosyUpstream {
  base_url?: string;
  reachable?: boolean;
  model_loaded?: boolean;
  sample_rate?: number;
  voices?: number;
  voices_dir?: string;
  last_probe_ts?: number | null;
  info?: Record<string, unknown> | null;
}

export interface NsfwUpstream {
  enabled?: boolean;
  base_url?: string;
  mode?: string;
  prewarm?: boolean;
  reachable?: boolean;
  health?: {
    ok?: boolean;
    main_ready?: boolean;
    aux_ready?: boolean;
    main_loaded?: boolean;
    aux_loaded?: boolean;
    anime_segm_ready?: boolean;
    anime_segm_loaded?: boolean;
    nevermore_ready?: boolean;
    nevermore_loaded?: boolean;
    wd_rating_ready?: boolean;
    wd_rating_loaded?: boolean;
    enabled?: Record<string, boolean>;
    device?: string;
    idle_sec?: number;
    model?: string;
    version?: string;
  } | null;
}

export interface DeviceResp {
  ts: number;
  host?: DeviceHost;
  upstreams?: { comfyui?: ComfyUpstream; cosyvoice?: CosyUpstream; nsfw?: NsfwUpstream };
  scheduler?: {
    queue_length?: number;
    running?: number;
    max_concurrent?: number;
    effective_concurrent?: number;
    throttle_reason?: string;
    total_queued?: number;
    total_completed?: number;
  };
  self_check?: SelfCheck | null;
}

// ---------- 配置（GET /config 摘要 / GET /config/raw 全量） ----------

export type ConfigSection = Record<string, unknown>;

export interface RuntimeConfig {
  config_file: string;
  server: ConfigSection & { tasks_enabled?: Record<string, boolean> };
  tts: ConfigSection;
  comfyui: ConfigSection;
  monitoring: ConfigSection;
  inspector: ConfigSection;
  nsfw?: ConfigSection;
  started_at?: number;
}

// ---------- WebSocket 消息 ----------

export type WsTasksMessage =
  | { event: "init"; tasks: Task[] }
  | { event: "task_update"; task: Task }
  | { event: "ping" };

export interface LogLine {
  ts: number;
  level: string;
  message: string;
}

// ---------- 外部服务面板 ----------

export interface ServiceInfo {
  name: string;
  label: string;
  state: "running" | "starting" | "unhealthy" | "stopped" | "error";
  health: "ok" | "fail" | "unknown";
  health_detail: string;
  health_url: string;
  alive: boolean;
  in_grace: boolean;
  pid: number | null;
  cpu_percent: number;
  rss_mb: number;
  uptime_s: number | null;
  managed: boolean;
  self: boolean;
  autostart: boolean;
  auto_restart: boolean;
  log: string;
  error?: string;
}

export interface ServicesResp {
  enabled: boolean;
  check_interval: number;
  start_grace: number;
  services: ServiceInfo[];
}

export interface ServicesConfigResp {
  services: Record<string, any>;
  alerts: Record<string, any>;
}

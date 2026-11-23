/** API 客户端：全部走相对路径（开发由 Vite 代理，生产与 FastAPI 同源）。 */
import type {
  DeviceResp,
  RuntimeConfig,
  StatsBrief,
  ServicesConfigResp,
  ServicesResp,
  StatsDetail,
  TasksPageData,
} from "./types";

export class ApiFailure extends Error {
  constructor(
    public code: string,
    public detail: string,
    public status = 0,
  ) {
    super(detail);
    this.name = "ApiFailure";
  }
}

interface ErrorBody {
  ok?: false;
  error?: string;
  msg?: string;
}

async function request<T>(path: string, init: RequestInit, timeoutMs: number): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(path, { ...init, signal: ctrl.signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiFailure("timeout", `请求超时（${Math.round(timeoutMs / 1000)}s）`, 0);
    }
    throw new ApiFailure("network_error", "无法连接 TaskHub 服务", 0);
  } finally {
    clearTimeout(timer);
  }

  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    /* 非 JSON 响应按空处理 */
  }

  if (!res.ok) {
    const b = (body ?? {}) as ErrorBody;
    throw new ApiFailure(b.error ?? `http_${res.status}`, b.msg ?? res.statusText, res.status);
  }
  return body as T;
}

function qs(params: Record<string, unknown>): string {
  const pairs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return pairs.length ? `?${pairs.join("&")}` : "";
}

// ---------- 设备 / 自检 ----------

export async function fetchDevice(raw = false): Promise<DeviceResp> {
  return request<DeviceResp>(`/device${raw ? "?raw=1" : ""}`, { method: "GET" }, 15_000);
}

export async function fetchSelfCheck(): Promise<unknown> {
  return request("/self-check", { method: "GET" }, 10_000);
}

export async function runSelfCheck(): Promise<unknown> {
  return request("/self-check/run", { method: "POST" }, 15_000);
}

// ---------- 统计 ----------

export async function fetchStats(hours = 24): Promise<StatsBrief> {
  return request<StatsBrief>(`/stats${qs({ hours })}`, { method: "GET" }, 10_000);
}

export async function fetchStatsDetail(hours = 24): Promise<StatsDetail> {
  return request<StatsDetail>(`/stats/detail${qs({ hours })}`, { method: "GET" }, 10_000);
}

// ---------- 任务 ----------

export type TaskQuery = {
  page: number;
  page_size: number;
  task_type?: string;
  status?: string;
  keyword?: string;
};

export async function fetchTasks(q: TaskQuery): Promise<TasksPageData> {
  return request<TasksPageData>(`/tasks${qs(q)}`, { method: "GET" }, 10_000);
}

export async function promoteTask(taskId: string, priority = -1): Promise<void> {
  await request(`/tasks/${encodeURIComponent(taskId)}/promote${qs({ priority })}`, { method: "POST" }, 10_000);
}

export async function cancelTask(taskId: string): Promise<void> {
  await request(`/tasks/${encodeURIComponent(taskId)}/cancel`, { method: "POST" }, 10_000);
}

// ---------- 配置 ----------

export async function fetchConfig(): Promise<RuntimeConfig> {
  return request<RuntimeConfig>("/config", { method: "GET" }, 10_000);
}

/** 全量可编辑配置（写回用） */
export async function fetchConfigRaw(): Promise<Record<string, Record<string, unknown>>> {
  return request("/config/raw", { method: "GET" }, 10_000);
}

export interface SaveConfigResult {
  ok: boolean;
  config_file: string;
  needs_restart: boolean;
  tasks_enabled?: Record<string, boolean>;
  note?: string;
}

export async function saveConfig(data: Record<string, Record<string, unknown>>): Promise<SaveConfigResult> {
  return request("/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }, 15_000);
}

// ---------- NSFW 调度控制 ----------

/** 预热 nsfw-check 模型（force=true 强制重载）。 */
export async function nsfwWarm(force = false): Promise<{ ok: boolean }> {
  return request(`/nsfw/warm${qs({ force: force ? 1 : 0 })}`, { method: "POST" }, 120_000);
}

/** 卸载 nsfw-check 模型、释放显存。 */
export async function nsfwRelease(): Promise<{ ok: boolean }> {
  return request("/nsfw/release", { method: "POST" }, 30_000);
}

export interface HealthResp {
  status: string;
  started_at?: number;
}

export async function fetchHealth(): Promise<HealthResp> {
  return request<HealthResp>("/health", { method: "GET" }, 5_000);
}

/** 触发服务重启（连接会中断，调用方需容错网络错误） */
export async function restartService(): Promise<{ ok: boolean; restarting: boolean; old_started_at?: number }> {
  return request("/restart", { method: "POST" }, 10_000);
}

/** 关闭服务（连接会中断） */
export async function shutdownService(): Promise<{ ok: boolean; stopping: boolean }> {
  return request("/shutdown", { method: "POST" }, 10_000);
}

/** 落盘日志尾部（历史回填） */
export async function fetchLogsTail(lines = 200): Promise<{ file: string; lines: string[]; error?: string }> {
  return request(`/logs/tail${qs({ lines })}`, { method: "GET" }, 10_000);
}

// ---------- 外部服务面板 ----------

export async function fetchServices(): Promise<ServicesResp> {
  return request("/services", { method: "GET" }, 15_000);
}

export type ServiceAction = "start" | "stop" | "restart";

export async function serviceAction(name: string, action: ServiceAction): Promise<Record<string, any>> {
  return request(`/services/${encodeURIComponent(name)}/${action}`, { method: "POST" }, 60_000);
}

export async function fetchServiceLogs(name: string, lines = 200): Promise<{ file: string; lines: string[] }> {
  return request(`/services/${encodeURIComponent(name)}/logs${qs({ lines })}`,
    { method: "GET" }, 10_000);
}

export async function fetchServicesConfig(): Promise<ServicesConfigResp> {
  return request("/services/config", { method: "GET" }, 10_000);
}

export async function saveServicesConfig(data: ServicesConfigResp): Promise<{ ok: boolean; config_file: string }> {
  return request("/services/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }, 15_000);
}

export async function testAlerts(): Promise<{ ok: boolean; results: Record<string, string> }> {
  return request("/services/alerts/test", { method: "POST" }, 30_000);
}

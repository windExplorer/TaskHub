/** 纯函数展示层：数字/时间格式化 */

/** null/NaN 安全的定长小数格式化，无效值显示 – */
export function fmt(n: number | null | undefined, d = 1): string {
  return n === null || n === undefined || Number.isNaN(n) ? "–" : Number(n).toFixed(d);
}

/** MM-DD HH:mm:ss（列表用） */
export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  const p = (v: number) => String(v).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** YYYY-MM-DD HH:mm:ss（跨年的历史任务用） */
export function fmtTimeFull(ts: number | null | undefined): string {
  if (!ts) return "–";
  const d = new Date(ts * 1000);
  const p = (v: number) => String(v).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 运行时长 → x天x小时 / x小时x分 / x分x秒 */
export function fmtUptime(s: number | null | undefined): string {
  if (s === null || s === undefined) return "–";
  s = Math.max(0, Math.round(s));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}天${h}小时`;
  if (h) return `${h}小时${m}分`;
  if (m) return `${m}分${s % 60}秒`;
  return `${s}秒`;
}

/** 统计图 x 轴刻度：小时桶显示 HH:mm，天桶显示 M/D */
export function fmtAxis(ts: number, bucket: "hour" | "day"): string {
  const d = new Date(ts * 1000);
  const p = (v: number) => String(v).padStart(2, "0");
  if (bucket === "day") return `${d.getMonth() + 1}/${d.getDate()}`;
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 当天时间 HH:mm:ss（入参为毫秒，如 Date.now() / ts*1000） */
export function fmtClock(ms: number): string {
  const d = new Date(ms);
  const p = (v: number) => String(v).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** 图表 y 轴的"好看"上限：向上取整到 4 的倍数（队列/并发这类小整数也要留头） */
export function niceMax(values: number[], floor = 4): number {
  const max = Math.max(floor, ...values);
  return Math.ceil(max / 4) * 4;
}

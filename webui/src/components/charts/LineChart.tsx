import { useId, useState } from "react";
import { motion } from "motion/react";
import { useWidth } from "../../hooks/useWidth";
import { niceMax } from "../../lib/ui";

export interface LineSeries {
  name: string;
  /** 任意 CSS 颜色（建议 var(--xxx) 语义色，深浅主题自动跟随） */
  color: string;
  data: number[];
  dashed?: boolean;
  /** 折线下方渐变面积 */
  fill?: boolean;
}

interface Props {
  series: LineSeries[];
  /** 与 data 等长的 x 轴标签 */
  labels: string[];
  height?: number;
  /** 固定 y 上限（百分比图传 100），缺省按数据自适应 */
  yMax?: number;
  unit?: string;
  decimals?: number;
  emptyText?: string;
}

const PAD_L = 38;
const PAD_R = 10;
const PAD_T = 10;
const PAD_B = 20;

/** 平滑路径：水平切线三次贝塞尔（监控曲线观感，轻微过冲可接受） */
function smoothPath(pts: Array<[number, number]>): string {
  const n = pts.length;
  if (n === 0) return "";
  if (n === 1) return `M${pts[0][0]},${pts[0][1]}`;
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 1; i < n; i++) {
    const [x0, y0] = pts[i - 1];
    const [x1, y1] = pts[i];
    if (n === 2) {
      d += ` L${x1},${y1}`;
    } else {
      const dx = (x1 - x0) / 2;
      d += ` C${x0 + dx},${y0} ${x1 - dx},${y1} ${x1},${y1}`;
    }
  }
  return d;
}

function fmtV(v: number | undefined, decimals: number, unit: string): string {
  return v === undefined || v === null || Number.isNaN(v) ? "–" : v.toFixed(decimals) + unit;
}

/** 手写 SVG 折线图（零图表库）：多序列 + 渐变面积 + hover 最近点 tooltip。 */
export function LineChart({
  series,
  labels,
  height = 190,
  yMax,
  unit = "",
  decimals = 1,
  emptyText = "等待数据…",
}: Props) {
  const [wrapRef, w] = useWidth<HTMLDivElement>();
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const gid = useId().replace(/[^a-zA-Z0-9]/g, "");
  // SVG id 不能含空格（序列名如 "CPU %"），否则 url(#id) 引用失效
  const gidFor = (name: string) => `g${gid}${name.replace(/[^a-zA-Z0-9]/g, "")}`;

  const n = series[0]?.data.length ?? 0;
  const plotW = Math.max(1, w - PAD_L - PAD_R);
  const plotH = Math.max(1, height - PAD_T - PAD_B);
  const top = yMax ?? niceMax(series.flatMap((s) => s.data));
  const yOf = (v: number) => PAD_T + plotH * (1 - Math.min(1, Math.max(0, (v - 0) / top)));
  const xOf = (i: number) => (n <= 1 ? PAD_L + plotW / 2 : PAD_L + (i / (n - 1)) * plotW);

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => top * f);
  const xCount = Math.min(6, Math.max(2, n));
  const xTicks = Array.from({ length: xCount }, (_, i) => {
    const idx = Math.round((i * (n - 1)) / (xCount - 1));
    return { idx, label: labels[idx] ?? "" };
  });

  const hoverX = hoverIdx !== null && n > 0 ? xOf(hoverIdx) : null;

  return (
    <div
      ref={wrapRef}
      className="relative select-none"
      style={{ height }}
      onMouseMove={(e) => {
        if (n === 0) return;
        const rect = e.currentTarget.getBoundingClientRect();
        const idx = Math.round(((e.clientX - rect.left - PAD_L) / plotW) * (n - 1));
        setHoverIdx(Math.max(0, Math.min(n - 1, idx)));
      }}
      onMouseLeave={() => setHoverIdx(null)}
    >
      {w > 0 && n > 0 ? (
        <svg width={w} height={height} className="overflow-visible">
          <defs>
            {series.map(
              (s) =>
                s.fill && (
                  <linearGradient key={s.name} id={gidFor(s.name)} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={s.color} stopOpacity={0.22} />
                    <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
                  </linearGradient>
                ),
            )}
          </defs>

          {/* 横向网格 + y 刻度 */}
          {yTicks.map((v, i) => (
            <g key={i}>
              <line
                x1={PAD_L}
                x2={w - PAD_R}
                y1={yOf(v)}
                y2={yOf(v)}
                stroke={i === 0 ? "var(--line-strong)" : "var(--line)"}
                strokeWidth={1}
              />
              <text
                x={PAD_L - 6}
                y={yOf(v)}
                dy="0.32em"
                textAnchor="end"
                className="fill-ink-3 font-mono"
                fontSize={10}
              >
                {Math.round(v)}
              </text>
            </g>
          ))}

          {/* x 刻度 */}
          {xTicks.map((t, i) => (
            <text
              key={i}
              x={xOf(t.idx)}
              y={height - 4}
              textAnchor={i === 0 ? "start" : i === xCount - 1 ? "end" : "middle"}
              className="fill-ink-3 font-mono"
              fontSize={10}
            >
              {t.label}
            </text>
          ))}

          {/* hover 引导线 */}
          {hoverX !== null && (
            <line
              x1={hoverX}
              x2={hoverX}
              y1={PAD_T}
              y2={PAD_T + plotH}
              stroke="var(--line-strong)"
              strokeDasharray="3 3"
            />
          )}

          {/* 序列：面积 + 折线（入场画线一次，数据每秒更新不重播） */}
          {series.map((s) => {
            const pts = s.data.map((v, i) => [xOf(i), yOf(v)] as [number, number]);
            const d = smoothPath(pts);
            const baseY = yOf(0);
            const areaD = d
              ? `${d} L${pts[pts.length - 1][0]},${baseY} L${pts[0][0]},${baseY} Z`
              : "";
            return (
              <g key={s.name}>
                {s.fill && areaD && (
                  <motion.path
                    d={areaD}
                    fill={`url(#${gidFor(s.name)})`}
                    stroke="none"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.7 }}
                  />
                )}
                {s.dashed ? (
                  <motion.path
                    d={d}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={1.2}
                    strokeDasharray="4 4"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.5 }}
                  />
                ) : (
                  <motion.path
                    d={d}
                    fill="none"
                    stroke={s.color}
                    strokeWidth={1.8}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.6 }}
                  />
                )}
              </g>
            );
          })}
        </svg>
      ) : (
        <div className="flex h-full items-center justify-center text-xs text-ink-3">{emptyText}</div>
      )}

      {/* hover tooltip */}
      {hoverIdx !== null && n > 0 && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-xl border border-line bg-surface px-3 py-2 shadow-card"
          style={{
            left: Math.min(Math.max(hoverX ?? 0, 80), w - 80),
            top: 4,
          }}
        >
          <div className="mb-1 font-mono text-[10px] text-ink-3">{labels[hoverIdx]}</div>
          {series.map((s) => (
            <div key={s.name} className="flex items-center gap-2 whitespace-nowrap text-xs">
              <span className="size-2 shrink-0 rounded-full" style={{ background: s.color }} />
              <span className="text-ink-3">{s.name}</span>
              <span className="tnum ml-auto font-mono text-ink">
                {fmtV(s.data[hoverIdx], decimals, unit)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 图例 */}
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 pl-10">
        {series.map((s) => (
          <span key={s.name} className="flex items-center gap-1.5 text-[11px] text-ink-3">
            <span className="h-[3px] w-4 rounded-full" style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}

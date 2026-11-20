import { motion } from "motion/react";

import { AnimatedNumber } from "../AnimatedNumber";

export interface DonutItem {
  label: string;
  value: number;
  color: string;
}

interface Props {
  items: DonutItem[];
  size?: number;
  thickness?: number;
  centerLabel?: string;
  emptyText?: string;
}

/** 手写 SVG 环形图（零图表库）：strokeDasharray 分段 + 底部图例，入场缩放淡入。 */
export function DonutChart({
  items,
  size = 200,
  thickness = 26,
  centerLabel = "总计",
  emptyText = "暂无数据",
}: Props) {
  const total = items.reduce((s, it) => s + it.value, 0);
  const cx = size / 2;
  const r = (size - thickness) / 2 - 6;
  const C = 2 * Math.PI * r;
  const gap = items.filter((it) => it.value > 0).length > 1 ? 2 : 0;

  // 累积偏移（负 strokeDashoffset 让分段依次接续）
  let acc = 0;
  const segments = items
    .filter((it) => it.value > 0)
    .map((it) => {
      const frac = it.value / total;
      const len = Math.max(0, frac * C - gap);
      const seg = { ...it, len, offset: -(acc * C) };
      acc += frac;
      return seg;
    });

  return (
    <div className="flex flex-col items-center gap-3">
      <motion.div
        initial={{ opacity: 0, scale: 0.88 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ type: "spring", stiffness: 260, damping: 24 }}
        className="relative"
        style={{ width: size, height: size }}
      >
        <svg width={size} height={size}>
          <g transform={`rotate(-90 ${cx} ${cx})`}>
            <circle
              cx={cx}
              cy={cx}
              r={r}
              fill="none"
              stroke="var(--surface-2)"
              strokeWidth={thickness}
            />
            {segments.map((s) => (
              <motion.circle
                key={s.label}
                cx={cx}
                cy={cx}
                r={r}
                fill="none"
                stroke={s.color}
                strokeWidth={thickness}
                strokeDasharray={`${s.len} ${C - s.len}`}
                strokeDashoffset={s.offset}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.5 }}
              />
            ))}
          </g>
        </svg>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="tnum font-mono text-2xl font-semibold text-ink">
            <AnimatedNumber value={total} format={(v) => String(Math.round(v))} />
          </span>
          <span className="mt-0.5 text-[11px] text-ink-3">{total > 0 ? centerLabel : emptyText}</span>
        </div>
      </motion.div>

      <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
        {segments.map((s) => (
          <span key={s.label} className="flex items-center gap-1.5 text-[11px] text-ink-3">
            <span className="size-2.5 rounded-full" style={{ background: s.color }} />
            {s.label}
            <span className="tnum font-mono text-ink-2">{s.value}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

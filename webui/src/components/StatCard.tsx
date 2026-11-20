import { motion } from "motion/react";

import { spring } from "../constants";

/** 指标卡：label + 大数字（children）+ 可选副行 */
export function StatCard({
  label,
  children,
  sub,
  delay = 0,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  sub?: React.ReactNode;
  delay?: number;
  className?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...spring, delay }}
      className={`theme-t rounded-2xl border border-line bg-surface/80 p-4 shadow-card backdrop-blur ${className}`}
    >
      <div className="mb-1 text-xs text-ink-3">{label}</div>
      <div className="flex items-baseline gap-1">{children}</div>
      {sub && <div className="mt-2 text-[11px] text-ink-3">{sub}</div>}
    </motion.div>
  );
}

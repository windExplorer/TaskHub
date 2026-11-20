import { motion } from "motion/react";

import { spring } from "../constants";

/** 玻璃拟态卡片（全站统一容器）：入场 y+fade，可配 delay 交错 */
export function Card({
  children,
  className = "",
  delay = 0,
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...spring, delay }}
      className={`theme-t rounded-2xl border border-line bg-surface/80 shadow-card backdrop-blur ${className}`}
    >
      {children}
    </motion.div>
  );
}

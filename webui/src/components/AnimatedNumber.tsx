import { useEffect } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "motion/react";

/** 数字滚动动画：value 变化时以弹簧过渡到新值。 */
export function AnimatedNumber({
  value,
  format,
  className,
}: {
  value: number;
  format: (v: number) => string;
  className?: string;
}) {
  const raw = useMotionValue(0);
  const spring = useSpring(raw, { stiffness: 90, damping: 20 });
  const text = useTransform(spring, (v) => format(v));

  useEffect(() => {
    raw.set(value);
  }, [value, raw]);

  return <motion.span className={className}>{text}</motion.span>;
}

import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

/** 测量元素内容宽度（图表按真实像素渲染 SVG，避免 preserveAspectRatio 拉伸变形） */
export function useWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 挂载即同步量一次（后台标签页里 ResizeObserver 回调可能迟迟不派发）
    setWidth(Math.round(el.getBoundingClientRect().width));
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width ?? 0;
      setWidth(Math.round(w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return [ref, width];
}

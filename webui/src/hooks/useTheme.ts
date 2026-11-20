import { useEffect, useState } from "react";

/** 主题三态：浅色 / 深色 / 跟随系统。与 index.html 内联脚本共用同一 localStorage key。 */
export type ThemeMode = "light" | "dark" | "system";

const KEY = "taskhub:theme";
const mq = "(prefers-color-scheme: dark)";

function apply(mode: ThemeMode): void {
  const dark =
    mode === "dark" ||
    (mode !== "light" && window.matchMedia(mq).matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

function loadMode(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "system";
  } catch {
    return "system";
  }
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(loadMode);

  useEffect(() => {
    try {
      localStorage.setItem(KEY, mode);
    } catch {
      /* 忽略隐私模式写入失败 */
    }
    apply(mode);

    if (mode !== "system") return;
    const mql = window.matchMedia(mq);
    const onChange = () => apply("system");
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [mode]);

  return { mode, setMode };
}

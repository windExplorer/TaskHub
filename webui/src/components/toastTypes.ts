import type { ToastKind } from "./Toast";

/** Toast push 函数的最小接口（避免页面组件依赖整个 useToasts 返回结构） */
export type ToastPush = (kind: ToastKind, title: string, detail?: string) => void;

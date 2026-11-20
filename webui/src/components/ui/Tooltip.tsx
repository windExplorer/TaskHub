import * as RTooltip from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

/** Radix Tooltip 封装：替换原生 title 属性，样式跟随主题。 */
export function Tooltip({
  content,
  children,
  side = "top",
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
}) {
  return (
    <RTooltip.Provider delayDuration={200}>
      <RTooltip.Root>
        <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
        <RTooltip.Portal>
          <RTooltip.Content
            side={side}
            sideOffset={6}
            className="z-[60] rounded-lg border border-line bg-surface px-2.5 py-1.5 text-xs text-ink shadow-card"
          >
            {content}
            <RTooltip.Arrow className="fill-surface" />
          </RTooltip.Content>
        </RTooltip.Portal>
      </RTooltip.Root>
    </RTooltip.Provider>
  );
}

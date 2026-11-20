import * as RRadio from "@radix-ui/react-radio-group";
import type { LucideIcon } from "lucide-react";

export interface RadioOption<T extends string> {
  value: T;
  label: string;
  icon: LucideIcon;
}

/** Radix RadioGroup 封装：图标式分段控件（主题切换用）。 */
export function IconRadioGroup<T extends string>({
  value,
  onValueChange,
  options,
  ariaLabel,
}: {
  value: T;
  onValueChange: (value: T) => void;
  options: RadioOption<T>[];
  ariaLabel?: string;
}) {
  return (
    <RRadio.Root
      value={value}
      onValueChange={(v) => onValueChange(v as T)}
      aria-label={ariaLabel}
      className="flex items-center gap-1 rounded-full border border-line bg-surface p-1 shadow-card"
    >
      {options.map((opt) => {
        const Icon = opt.icon;
        return (
          <RRadio.Item
            key={opt.value}
            value={opt.value}
            aria-label={opt.label}
            className="flex size-8 items-center justify-center rounded-full text-ink-3 transition-colors hover:text-ink data-[state=checked]:bg-accent data-[state=checked]:text-on-accent"
          >
            <Icon size={16} className="transition-transform group-hover:scale-110" />
          </RRadio.Item>
        );
      })}
    </RRadio.Root>
  );
}

import * as RSelect from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";

export interface SelectOption {
  value: string;
  label: string;
}

/** Radix Select 封装：外观对齐原生 select，样式跟随主题。 */
export function Select({
  value,
  onValueChange,
  options,
  placeholder = "请选择…",
  disabled,
  ariaLabel,
  className = "",
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  ariaLabel?: string;
  className?: string;
}) {
  // Radix 禁止空字符串作为 Item value，用哨兵值映射
  const EMPTY = "__radix_empty__";
  const rootValue = value === "" ? EMPTY : value;
  const handleChange = (v: string) => onValueChange(v === EMPTY ? "" : v);

  return (
    <RSelect.Root value={rootValue} onValueChange={handleChange} disabled={disabled}>
      <RSelect.Trigger
        aria-label={ariaLabel}
        className={`flex w-full items-center justify-between gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-ink outline-none transition focus:border-accent disabled:cursor-not-allowed disabled:opacity-50 data-[placeholder]:text-ink-3 ${className}`}
      >
        <RSelect.Value placeholder={placeholder} />
        <RSelect.Icon>
          <ChevronDown size={15} className="shrink-0 text-ink-3" />
        </RSelect.Icon>
      </RSelect.Trigger>
      <RSelect.Portal>
        <RSelect.Content
          position="popper"
          sideOffset={4}
          className="z-50 max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-lg border border-line bg-surface p-1 shadow-card"
        >
          <RSelect.ScrollUpButton className="flex h-6 items-center justify-center text-ink-3">
            <ChevronUp size={14} />
          </RSelect.ScrollUpButton>
          <RSelect.Viewport>
            {options.map((opt) => (
              <RSelect.Item
                key={opt.value}
                value={opt.value === "" ? EMPTY : opt.value}
                className="relative flex cursor-pointer select-none items-center rounded-md py-1.5 pl-8 pr-3 text-sm text-ink-2 outline-none transition data-[highlighted]:bg-surface-2 data-[highlighted]:text-ink data-[state=checked]:text-accent"
              >
                <RSelect.ItemIndicator className="absolute left-2 inline-flex items-center">
                  <Check size={14} />
                </RSelect.ItemIndicator>
                <RSelect.ItemText>{opt.label}</RSelect.ItemText>
              </RSelect.Item>
            ))}
          </RSelect.Viewport>
          <RSelect.ScrollDownButton className="flex h-6 items-center justify-center text-ink-3">
            <ChevronDown size={14} />
          </RSelect.ScrollDownButton>
        </RSelect.Content>
      </RSelect.Portal>
    </RSelect.Root>
  );
}

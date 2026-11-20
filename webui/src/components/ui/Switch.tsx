import * as RSwitch from "@radix-ui/react-switch";

/** Radix Switch 封装：外观对齐 TaskHub 原开关（小号 accent 轨道 + 白色滑块）。 */
export function Switch({
  checked,
  onCheckedChange,
  disabled,
  ariaLabel,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  return (
    <RSwitch.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      aria-label={ariaLabel}
      className="relative h-5 w-9 shrink-0 cursor-pointer rounded-full transition-colors data-[state=checked]:bg-accent data-[state=unchecked]:bg-line-strong disabled:cursor-not-allowed disabled:opacity-40"
    >
      <RSwitch.Thumb className="absolute top-0.5 size-4 rounded-full bg-white shadow transition-all data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0.5" />
    </RSwitch.Root>
  );
}

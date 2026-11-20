import * as RSlider from "@radix-ui/react-slider";

/** Radix Slider 封装：外观对齐原生 range（accent 轨道 + 圆点滑块）。 */
export function Slider({
  value,
  onValueChange,
  min = 0,
  max = 1,
  step = 0.01,
  disabled,
  ariaLabel,
}: {
  value: number;
  onValueChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  return (
    <RSlider.Root
      value={[value]}
      onValueChange={([v]) => onValueChange(v)}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      aria-label={ariaLabel}
      className="relative flex h-5 w-full touch-none select-none items-center"
    >
      <RSlider.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-surface-2">
        <RSlider.Range className="absolute h-full rounded-full bg-accent" />
      </RSlider.Track>
      <RSlider.Thumb className="block size-4 rounded-full border-2 border-accent bg-surface shadow transition focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-50" />
    </RSlider.Root>
  );
}

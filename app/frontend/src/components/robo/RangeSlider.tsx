/**
 * RangeSlider.tsx
 * ================
 * Single labeled weight slider for the Robo Strategy questionnaire. No
 * slider primitive exists in this app's components/ui/ (no @radix-ui/react-
 * slider dependency) — this wraps a native <input type="range"> styled with
 * Tailwind's `accent-brand` utility, which needs no extra CSS and matches
 * the app's --brand token automatically in both themes.
 */
interface RangeSliderProps {
  label: string;
  value: number;
  onChange: (next: number) => void;
  disabled?: boolean;
}

export function RangeSlider({ label, value, onChange, disabled }: RangeSliderProps) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="w-40 shrink-0 text-[13px] text-foreground truncate">{label}</span>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1 h-2 rounded-full bg-muted accent-brand cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
      />
      <span className="w-12 shrink-0 text-right text-[13px] font-mono font-semibold text-foreground tabular-nums">
        {Math.round(value)}%
      </span>
    </div>
  );
}

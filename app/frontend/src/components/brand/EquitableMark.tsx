/**
 * EquitableMark.tsx
 * ==================
 * The "equal coin" brand mark — an equals sign struck into a coin.
 * Philosophy: equal access to institutional-grade analysis, for everyone.
 * Two flat shapes only (coin + equals), no gradients — reads clean at
 * favicon size and scales up cleanly for the login screen.
 *
 * Colours are pulled from CSS variables (--hero / --primary) so the mark
 * follows the app's light/dark theme automatically instead of being baked
 * to one palette.
 */
export function EquitableMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="Equitable"
      className={className}
    >
      <rect width="64" height="64" rx="15" fill="hsl(var(--hero))" />
      <circle cx="32" cy="32" r="17" fill="hsl(var(--primary))" />
      <rect x="24" y="26.6" width="16" height="4.6" rx="2.3" fill="hsl(var(--hero))" />
      <rect x="24" y="32.8" width="16" height="4.6" rx="2.3" fill="hsl(var(--hero))" />
    </svg>
  );
}

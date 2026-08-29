/**
 * EquitableMark.tsx
 * ==================
 * The "equal coin" brand mark — an equals sign struck into a coin.
 * Philosophy: equal access to institutional-grade analysis, for everyone.
 * Two flat shapes only (coin + equals), no gradients — reads clean at
 * favicon size and scales up cleanly for the login screen.
 *
 * Colour: the mark is a full-contrast inversion, drawn from --foreground and
 * --background. Those two are guaranteed to oppose each other in both themes,
 * which is the whole requirement here — the disc and the bars are *knocked out*
 * of the ground, so if the two fills ever resolve to the same value the mark
 * collapses into a featureless square.
 *
 * That is exactly what happened when the Uber Base re-skin landed: the mark
 * used --hero for the ground and --primary for the disc, and in light mode both
 * became #000000 (--primary went black to drive the inverted CTAs). Do not
 * reintroduce a semantic token here — those are free to change meaning. The
 * foreground/background pair is the only one whose contrast is load-bearing.
 *
 *   light → black coin, white disc  (identical to favicon.svg / the PWA icon)
 *   dark  → white coin, black disc  (inverted, so the silhouette still reads
 *           against the near-black L1 sidebar rather than vanishing into it)
 */
export function EquitableMark({ size = 28, className = '' }: { size?: number; className?: string }) {
  const ground = 'hsl(var(--foreground))';
  const knockout = 'hsl(var(--background))';
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="Equitable"
      className={className}
    >
      <rect width="64" height="64" rx="15" fill={ground} />
      <circle cx="32" cy="32" r="17" fill={knockout} />
      <rect x="24" y="26.6" width="16" height="4.6" rx="2.3" fill={ground} />
      <rect x="24" y="32.8" width="16" height="4.6" rx="2.3" fill={ground} />
    </svg>
  );
}

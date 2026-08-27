/**
 * RationaleBlock — the PM thesis, rendered with its themes intact.
 *
 * The portfolio-manager rationale arrives as several distinct themes separated
 * by newlines (older runs used "• " bullets, newer ones number them). Rendering
 * it as a single string collapses those themes into one dense wall of text,
 * which is what the desktop report used to do while mobile split them properly.
 * This component is the shared implementation so the two paths cannot drift
 * apart again — `ReportViewPage` (desktop) and `V2ReportView` (mobile) both
 * render it.
 *
 * When every line carries a bullet or number marker the block renders as a real
 * <ul>/<ol>, which is both better typography and better for screen readers.
 * Mixed or unmarked content falls back to one paragraph per line.
 */

/** Leading "• ", "- ", "* " or "1. " / "1) " marker. */
const BULLET = /^[•·▪◦‣*-]\s+/;
const NUMBERED = /^\d+[.)]\s+/;

export function RationaleBlock({
  text,
  className = '',
  itemClassName = 'text-sm text-foreground/85 leading-relaxed',
}: {
  text: string;
  className?: string;
  /** Applied to each paragraph / list item, so callers control the type scale. */
  itemClassName?: string;
}) {
  const lines = String(text)
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean);

  if (lines.length === 0) return null;

  const allBulleted = lines.length > 1 && lines.every((l) => BULLET.test(l));
  const allNumbered = lines.length > 1 && lines.every((l) => NUMBERED.test(l));

  if (allBulleted || allNumbered) {
    const marker = allBulleted ? BULLET : NUMBERED;
    const List = allNumbered ? 'ol' : 'ul';
    return (
      <List
        className={`${allNumbered ? 'list-decimal' : 'list-disc'} pl-5 space-y-2 marker:text-muted-foreground ${className}`}
      >
        {lines.map((l, i) => (
          <li key={i} className={`${itemClassName} pl-1`}>
            {l.replace(marker, '')}
          </li>
        ))}
      </List>
    );
  }

  return (
    <div className={`space-y-2 ${className}`}>
      {lines.map((l, i) => (
        <p key={i} className={itemClassName}>
          {l}
        </p>
      ))}
    </div>
  );
}

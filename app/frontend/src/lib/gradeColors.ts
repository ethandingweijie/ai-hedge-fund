/**
 * Shared VGPM grade color utility — graduated within each letter band.
 *
 * A+ dark green · A mid green · A- light green
 * B+ dark blue  · B mid blue  · B- light blue
 * C             amber
 * D             red
 */

export function gradeColorClass(grade?: string): string {
  if (!grade || grade === '—') return 'text-muted-foreground bg-muted/40';

  // ── A band ───────────────────────────────────────────────────────────────────
  if (grade === 'A+') return 'bg-emerald-600/20 text-emerald-800 dark:text-emerald-300';
  if (grade === 'A')  return 'bg-green-500/15  text-green-700   dark:text-green-400';
  if (grade === 'A-') return 'bg-green-400/10  text-green-600   dark:text-green-500';

  // ── B band ───────────────────────────────────────────────────────────────────
  if (grade === 'B+') return 'bg-blue-600/20   text-blue-800    dark:text-blue-300';
  if (grade === 'B')  return 'bg-blue-500/15   text-blue-700    dark:text-blue-400';
  if (grade === 'B-') return 'bg-blue-400/10   text-blue-600    dark:text-blue-500';

  // ── C ────────────────────────────────────────────────────────────────────────
  if (grade === 'C')  return 'bg-amber-500/15  text-amber-700   dark:text-amber-400';

  // ── D ────────────────────────────────────────────────────────────────────────
  if (grade === 'D')  return 'bg-red-500/15    text-red-700     dark:text-red-400';

  return 'text-muted-foreground bg-muted/40';
}

/** Convert PascalCase/CamelCase sector keys to display strings with spaces.
 *  e.g. "ProfessionalServices" → "Professional Services"
 *       "RealEstate" → "Real Estate"
 */
export function formatSector(s?: string | null): string | null {
  if (!s) return null;
  return s
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2');
}

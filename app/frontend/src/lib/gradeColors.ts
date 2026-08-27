/**
 * Shared VGPM grade chip styling — monochrome ordinal ramp.
 *
 * This was a rainbow scale (green A band · blue B band · amber C · red D).
 * Under the Uber Base system green and red are reserved for price change, and
 * a grade is a quality verdict, not a price move. Stripping only the green and
 * red would have left an incoherent scale that was monochrome at the ends and
 * chromatic in the middle, so the whole ramp is monochrome: the grade reads
 * through *prominence*, with A+ as a solid inverted chip down to D barely
 * lifting off the surface. The letter itself still carries the precise value.
 */
import { rankTone } from './semanticColors';

export function gradeColorClass(grade?: string): string {
  if (!grade || grade === '—') return rankTone(null);

  switch (grade) {
    // ── A band — strongest prominence ──────────────────────────────────────
    case 'A+':
    case 'A':
      return rankTone(0);
    case 'A-':
      return rankTone(1);

    // ── B band ─────────────────────────────────────────────────────────────
    case 'B+':
    case 'B':
      return rankTone(1);
    case 'B-':
      return rankTone(2);

    // ── C / D — recede ─────────────────────────────────────────────────────
    case 'C':
      return rankTone(2);
    case 'D':
      return rankTone(3);

    default:
      return rankTone(null);
  }
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

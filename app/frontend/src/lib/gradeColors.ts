/**
 * Sector label helper.
 *
 * The VGPM grade palette that used to live here now lives in
 * lib/semanticColors.ts as gradeTone() -- see the note there on why only the
 * A band carries colour.
 */

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

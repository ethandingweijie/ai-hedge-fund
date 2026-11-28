/**
 * China A-shares (SSE ".SS" / SZSE ".SZ") -- the frontend twin of src/markets/china.py.
 *
 * Canonical form is the 6-digit code plus the venue suffix ("600519.SS", "300750.SZ"). The backend canonicalises
 * every ticker the same way (src/tools/ticker_canonical.py); this copy exists so per-ticker dict lookups work while
 * a run is live and the user typed "600519" or "SH600519".
 */

const SSE_PREFIXES = ['600', '601', '603', '605', '688', '689'];
const SZSE_PREFIXES = ['000', '001', '002', '003', '300', '301'];

function venueForCode(code: string): 'SS' | 'SZ' | null {
  const p = code.slice(0, 3);
  if (SSE_PREFIXES.includes(p)) return 'SS';
  if (SZSE_PREFIXES.includes(p)) return 'SZ';
  return null;
}

/** "600519.SS" / "000858.SZ", or null when this is not a supported A-share stock code. */
export function cnCanonical(ticker: string): string | null {
  const t = (ticker || '').trim().toUpperCase();
  let m = t.match(/^(\d{6})\.(SS|SH|SHH|SZ|SHZ)$/);
  if (m) {
    const want = ['SS', 'SH', 'SHH'].includes(m[2]) ? 'SS' : 'SZ';
    return venueForCode(m[1]) === want ? `${m[1]}.${want}` : null;
  }
  m = t.match(/^(SH|SZ)\.?(\d{6})$/);
  if (m) {
    const want = m[1] === 'SH' ? 'SS' : 'SZ';
    return venueForCode(m[2]) === want ? `${m[2]}.${want}` : null;
  }
  // A bare six-digit code is NOT an A-share by itself (owner, 2026-11-28): Korea uses six digits too, so the
  // user picks the listing from the search list.
  return null;
}

export function isCnTicker(ticker: string): boolean {
  return cnCanonical(ticker) !== null;
}

/** One label for renminbi everywhere: "RMB", never a bare "¥" (also the yen sign). */
export const CN_CURRENCY_LABEL = 'RMB ';

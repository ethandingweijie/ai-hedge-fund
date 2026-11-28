/**
 * Operator details for the Terms, Privacy Policy and Refund Policy (owner, 2026-11-28: Singapore Pte Ltd, Singapore
 * law, PDPA). One place, so the three documents and the in-app notices cannot disagree.
 */
export const LEGAL = {
  /** Registered name exactly as on ACRA BizFile, e.g. "Equitable Research Pte. Ltd." */
  entity: '',
  /** ACRA Unique Entity Number */
  uen: '',
  /** Data Protection Officer contact (PDPA s 11(5)) and refund requests */
  email: '',
  /** Trading name shown in the app */
  brand: 'Equitable',
  effective: '28 November 2026',
  governingLaw: 'the laws of the Republic of Singapore',
};

/** "Equitable Research Pte. Ltd. (UEN 2026…)" or the brand while the entity details are being filled in. */
export function operatorLine(): string {
  if (!LEGAL.entity) return LEGAL.brand;
  return LEGAL.uen ? `${LEGAL.entity} (UEN ${LEGAL.uen})` : LEGAL.entity;
}

export function contactEmail(): string {
  return LEGAL.email || 'the contact address in your account settings';
}

/** The short notice every report and answer carries (web + mobile share it). */
export const RESEARCH_NOTICE =
  'AI-generated research for general information only. It does not take into account your investment objectives, ' +
  'financial situation or particular needs, is not financial advice or an offer to buy or sell any security, and may ' +
  'contain errors. Consider seeking advice from a licensed financial adviser before investing.';

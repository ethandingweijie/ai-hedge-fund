/**
 * One rating vocabulary for every report surface (owner, 2026-11-27).
 *
 * The backend speaks two dialects for one call: the research rating
 * (Overweight / Neutral / Underweight, relative to a benchmark over 12 months)
 * and the trade action (BUY / HOLD / SELL / SHORT / COVER). A novice saw five
 * words for one opinion. The page now leads with Buy / Hold / Sell everywhere
 * and keeps the research word as a secondary label; Under Review and Unrated
 * stay as they are (they are states, not opinions).
 *
 * Display only: nothing here changes how a rating is decided.
 */

export type PlainRating = 'Buy' | 'Hold' | 'Sell' | 'Under review' | 'Unrated';

export interface RatingWords {
  /** Buy / Hold / Sell (or Under review / Unrated). */
  primary: PlainRating;
  /** The research word ("Overweight") or the trade detail ("short position"), when it adds something. */
  secondary: string | null;
  /** actionTone() key for the pill. */
  toneKey: string;
}

const RESEARCH_TO_PLAIN: Record<string, PlainRating> = {
  OVERWEIGHT: 'Buy', NEUTRAL: 'Hold', UNDERWEIGHT: 'Sell',
  UNDER_REVIEW: 'Under review', UNRATED: 'Unrated',
};
const RESEARCH_WORD: Record<string, string> = {
  OVERWEIGHT: 'Overweight', NEUTRAL: 'Neutral', UNDERWEIGHT: 'Underweight',
};
const PLAIN_TO_RESEARCH: Record<string, string> = { Buy: 'Overweight', Hold: 'Neutral', Sell: 'Underweight' };
const ACTION_TO_PLAIN: Record<string, PlainRating> = {
  BUY: 'Buy', STRONG_BUY: 'Buy', HOLD: 'Hold', SELL: 'Sell', SHORT: 'Sell', COVER: 'Hold',
};
const TONE: Record<PlainRating, string> = {
  Buy: 'BUY', Hold: 'HOLD', Sell: 'SELL', 'Under review': 'UNDER_REVIEW', Unrated: 'UNRATED',
};

const norm = (s?: string | null) => (s ?? '').trim().toUpperCase().replace(/[\s-]+/g, '_');

/** From a research rating or its label ("Overweight", "NEUTRAL", "Under Review", "Unrated — pre-revenue"). */
export function wordsFromResearch(rating?: string | null, opts: { underReview?: boolean } = {}): RatingWords | null {
  if (opts.underReview) return { primary: 'Under review', secondary: null, toneKey: TONE['Under review'] };
  const k = norm(rating);
  if (!k) return null;
  if (k.startsWith('UNRATED')) return { primary: 'Unrated', secondary: null, toneKey: TONE.Unrated };
  if (k.startsWith('UNDER_REVIEW')) return { primary: 'Under review', secondary: null, toneKey: TONE['Under review'] };
  const p = RESEARCH_TO_PLAIN[k];
  if (!p) return null;
  return { primary: p, secondary: RESEARCH_WORD[k] ?? null, toneKey: TONE[p] };
}

/** From a trade action (BUY / HOLD / SELL / SHORT / COVER). */
export function wordsFromAction(action?: string | null): RatingWords | null {
  const k = norm(action);
  const p = ACTION_TO_PLAIN[k];
  if (!p) return null;
  const secondary = k === 'SHORT' ? 'short position' : k === 'COVER' ? 'close a short' : PLAIN_TO_RESEARCH[p] ?? null;
  return { primary: p, secondary, toneKey: TONE[p] };
}

/** Research rating first (it is the published opinion), trade action as the fallback. */
export function ratingWords(src: { research_rating?: string | null; rating_label?: string | null; under_review?: boolean; action?: string | null }): RatingWords | null {
  return wordsFromResearch(src.research_rating ?? src.rating_label, { underReview: src.under_review })
    ?? wordsFromResearch(src.rating_label)
    ?? wordsFromAction(src.action);
}

/**
 * The visible one-liner under a rating: what the word means against the
 * benchmark the rating was set on. The thresholds mirror
 * ratings.py's 500 bps band (Overweight beats the benchmark by at least 5 points).
 * Pass the i18n `t`; the templates are zh keys.
 */
export const ONE_LINER: Record<PlainRating, string> = {
  Buy: 'Buy = expected to beat the {b} by at least 5 percentage points over 12 months (price change plus dividends).',
  Hold: 'Hold = expected to roughly match the {b} over 12 months (within 5 percentage points, price change plus dividends).',
  Sell: 'Sell = expected to trail the {b} by at least 5 percentage points over 12 months (price change plus dividends).',
  'Under review': 'Under review = news since our last report is not yet in our numbers, so the rating waits until it is.',
  Unrated: 'Unrated = we do not publish a value or rating for this stock yet.',
};

export function ratingOneLiner(
  primary: PlainRating,
  benchmarkName: string | null | undefined,
  t: (s: string, v?: Record<string, string | number>) => string,
): string {
  const b = (benchmarkName || t('market')).replace(/^the\s+/i, '');
  return t(ONE_LINER[primary], { b });
}

/**
 * Numbers inside generated text that restate the target at a different
 * precision ("PT S$3.5" beside a S$3.54 target) are rewritten to the target's
 * own two decimals, so the page shows one figure. Only a "PT" / "target"
 * figure within half a cent-rounding step of the target is touched.
 */
export function alignTargetInText(text: string, target: number | null | undefined, sym: string): string {
  if (!text || target == null || !isFinite(target)) return text;
  const re = /\b(PT|price target|target)(\s+(?:of\s+)?)((?:US|S|HK|A|C)?\$|RMB\s?|¥)?\s?(\d+(?:\.\d+)?)/gi;
  return text.replace(re, (m, word: string, gap: string, _cur: string | undefined, num: string) => {
    const v = Number(num);
    const decimals = (num.split('.')[1] ?? '').length;
    if (!isFinite(v) || decimals >= 2) return m;
    const step = Math.pow(10, -decimals) / 2;
    if (Math.abs(v - target) > step + 1e-9) return m;
    return `${word}${gap}${sym}${target.toFixed(2)}`;
  });
}

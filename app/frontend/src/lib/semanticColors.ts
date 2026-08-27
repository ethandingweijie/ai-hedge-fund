/**
 * Semantic colour vocabulary — Uber Base monochrome system.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THE RULE: chromatic green and red mean PRICE CHANGE. Nothing else.
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * On a trading screen the eye should be able to trust that a green number went
 * up and a red number went down. Every additional green/red — a passing check,
 * an "approved" pill, a BUY tag, a healthy progress bar — spends a little of
 * that trust, until the colour carries no information at all. So the palette is
 * partitioned into three non-overlapping roles:
 *
 *   1. `priceTone()`      → gain / loss / flat. Chromatic. Price deltas, %
 *                           upside, returns, P&L. THE ONLY green and red.
 *   2. `alertTone()`      → errors and caution. MONOCHROME for errors: the
 *                           rule admits no exception, so a failure reads via
 *                           full-contrast text + an alert glyph + an elevated
 *                           container, never via red. Amber `--warning`
 *                           survives, being neither green nor red.
 *   3. `emphasisTone()`   → monochrome. Everything else: signals, verdicts,
 *      `badgeTone()`        statuses, match quality, health, pass/fail. Rank is
 *                           carried by surface tier + text emphasis + weight,
 *                           which is what the Base system uses depth for.
 *
 * If you are reaching past bucket 1 for green or red, the answer is bucket 3.
 */

/** Direction of a price movement. */
export type PriceDirection = 'up' | 'down' | 'flat';

/**
 * Chromatic tone for a PRICE CHANGE only.
 *
 * @param value  signed magnitude; 0 / null / undefined render as flat
 * @param opts.background  also tint a container behind the number
 */
export function priceTone(
  value: number | null | undefined,
  opts: { background?: boolean } = {},
): string {
  const dir: PriceDirection =
    value == null || value === 0 ? 'flat' : value > 0 ? 'up' : 'down';

  if (dir === 'flat') {
    return opts.background ? 'text-content-muted bg-surface-2' : 'text-content-muted';
  }
  const text = dir === 'up' ? 'text-gain' : 'text-loss';
  if (!opts.background) return text;
  return dir === 'up' ? `${text} bg-gain/10` : `${text} bg-loss/10`;
}

/** Bare colour class for a price direction, when you already know the sign. */
export function priceToneFor(dir: PriceDirection): string {
  return dir === 'up' ? 'text-gain' : dir === 'down' ? 'text-loss' : 'text-content-muted';
}

/**
 * Failure and caution states — NOT price movement.
 *
 * Errors are monochrome. Red is spent entirely on price direction, so a
 * failure earns attention through contrast and iconography instead of hue —
 * pair this with an alert glyph and a `surface-2` container so the state stays
 * unmistakable without borrowing the price palette. Amber `--warning` is
 * retained: the rule governs green and red specifically.
 */
export function alertTone(level: 'error' | 'warning' | 'none'): string {
  switch (level) {
    case 'error':
      return 'text-content-high font-medium';
    case 'warning':
      return 'text-warning';
    default:
      return 'text-content-muted';
  }
}

/**
 * Monochrome emphasis ramp — the default for all non-price meaning.
 *
 * Rank reads through weight and luminance instead of hue:
 *   strong  → full-contrast text, heaviest
 *   medium  → body-copy emphasis
 *   muted   → metadata
 *   ghost   → disabled / placeholder
 */
export type Emphasis = 'strong' | 'medium' | 'muted' | 'ghost';

export function emphasisTone(level: Emphasis): string {
  switch (level) {
    case 'strong':
      return 'text-content-high font-semibold';
    case 'medium':
      return 'text-content-medium';
    case 'muted':
      return 'text-content-muted';
    case 'ghost':
      return 'text-content-disabled';
  }
}

/**
 * Monochrome badge/pill styling for signals, verdicts and statuses.
 *
 * `solid`   — highest prominence: inverted fill (the Base primary CTA look).
 * `outline` — mid prominence: hairline rim on the elevated surface.
 * `subtle`  — lowest: surface tint only, no rim.
 *
 * Prominence, not hue, is how a BUY reads louder than a HOLD.
 */
export type BadgeProminence = 'solid' | 'outline' | 'subtle';

export function badgeTone(prominence: BadgeProminence): string {
  switch (prominence) {
    case 'solid':
      return 'bg-primary text-primary-foreground border border-transparent font-semibold';
    case 'outline':
      return 'bg-surface-2 text-content-high border border-[var(--hairline)] font-medium';
    case 'subtle':
      return 'bg-surface-2 text-content-muted border border-transparent';
  }
}

/**
 * Directional investment signals (BUY / SELL / HOLD …).
 *
 * Monochrome by design: a recommendation is not a price change. Direction is
 * conveyed by the caret/arrow glyph and the label itself; prominence conveys
 * conviction. Callers that also show an expected % upside get their green or
 * red there, on the number, where it belongs.
 */
const SIGNAL_PROMINENCE: Record<string, BadgeProminence> = {
  STRONG_BUY: 'solid',
  BUY: 'solid',
  ACCUMULATE: 'outline',
  OVERWEIGHT: 'outline',
  HOLD: 'outline',
  NEUTRAL: 'subtle',
  UNDERWEIGHT: 'outline',
  REDUCE: 'outline',
  SELL: 'solid',
  STRONG_SELL: 'solid',
  SHORT: 'solid',
};

export function signalTone(signal: string | null | undefined): string {
  const key = (signal ?? '').toUpperCase().replace(/[\s-]+/g, '_');
  return badgeTone(SIGNAL_PROMINENCE[key] ?? 'subtle');
}

/**
 * Pass / fail / partial checks — monochrome.
 * The glyph (check, cross, dash) carries the meaning; colour would be
 * redundant here and would compete with real price signal on the same screen.
 */
export function checkTone(state: 'pass' | 'fail' | 'partial' | 'unknown'): string {
  switch (state) {
    case 'pass':
      return 'text-content-high';
    case 'fail':
      return 'text-content-muted line-through decoration-content-disabled';
    case 'partial':
      return 'text-content-medium';
    default:
      return 'text-content-disabled';
  }
}

/**
 * Ordinal ramp for graded scores — composite scores, conviction bands,
 * complacency tiers, verdict chips.
 *
 * These were previously rainbow scales (green → blue → amber → orange → red).
 * Stripping only green and red would have left an incoherent scale that was
 * monochrome at the ends and chromatic in the middle, so the whole ramp is
 * monochrome and rank reads through *prominence*: a top-band chip is a solid
 * inverted pill, a bottom-band chip barely lifts off the surface.
 *
 * Direction, where it exists (inflow vs outflow, long vs short), is carried by
 * the adjacent label — monochrome cannot encode sign, and should not pretend to.
 *
 * @param level 0 = strongest / top band … 3 = weakest; null = no data
 */
export type RankLevel = 0 | 1 | 2 | 3 | null;

export function rankTone(level: RankLevel): string {
  switch (level) {
    case 0:
      return 'bg-primary text-primary-foreground font-semibold';
    case 1:
      return 'bg-surface-2-active text-content-high border border-[var(--hairline)] font-medium';
    case 2:
      return 'bg-surface-2 text-content-high';
    case 3:
      return 'bg-surface-2 text-content-muted';
    default:
      return 'bg-muted text-muted-foreground';
  }
}

/** Text-only variant of {@link rankTone}, for scores rendered as bare numbers. */
export function rankTextTone(level: RankLevel): string {
  switch (level) {
    case 0:
      return 'text-content-high font-semibold';
    case 1:
      return 'text-content-high';
    case 2:
      return 'text-content-medium';
    case 3:
      return 'text-content-muted';
    default:
      return 'text-muted-foreground';
  }
}

/**
 * Trade-action pills (BUY / SELL / SHORT / HOLD / COVER).
 *
 * Colour is spent only on the two actions that ask the reader to OPEN a
 * position, because those are the ones worth spotting in a list:
 *   BUY   → cobalt `--brand`   (go long)
 *   SHORT → amber  `--warning` (go short — the contrarian, riskier call)
 * SELL and HOLD are monochrome: SELL closes an existing position and HOLD asks
 * for nothing, so neither earns an accent. HOLD previously owned amber, which
 * is what frees that token for SHORT without the two competing.
 *
 * Note the two accents are deliberately NOT green/red. Those remain reserved
 * for price change, so a coloured action pill can never be mistaken for a
 * price move sitting next to it. Direction reads from the label.
 */
export type ActionVariant = 'pill' | 'text';

const ACTION_TONES: Record<string, Record<ActionVariant, string>> = {
  BUY: {
    pill: 'bg-brand text-white border border-transparent',
    text: 'text-brand',
  },
  STRONG_BUY: {
    pill: 'bg-brand text-white border border-transparent',
    text: 'text-brand',
  },
  SHORT: {
    pill: 'bg-warning text-black border border-transparent',
    text: 'text-warning',
  },
  SELL: {
    pill: 'bg-surface-2 text-content-high border border-[var(--hairline)]',
    text: 'text-content-high',
  },
  HOLD: {
    pill: 'bg-surface-2 text-content-high border border-[var(--hairline)]',
    text: 'text-content-high',
  },
  COVER: {
    pill: 'bg-surface-2 text-content-high border border-[var(--hairline)]',
    text: 'text-content-high',
  },
};

export function actionTone(
  action: string | null | undefined,
  variant: ActionVariant = 'pill',
): string {
  const key = (action ?? '').toUpperCase().replace(/[\s-]+/g, '_');
  const entry = ACTION_TONES[key];
  if (entry) return entry[variant];
  return variant === 'pill' ? 'bg-muted text-muted-foreground' : 'text-muted-foreground';
}

/**
 * VGPM grade pills.
 *
 * Only the A band (A+, A, A-) carries colour — the cobalt `--brand` accent,
 * graduated by fill weight so A+ reads stronger than A-. B through D drop to
 * plain neutral text with no fill at all, so on a scorecard the eye lands on
 * genuine distinction and slides past the rest. The letter itself still states
 * the precise grade, so nothing is lost by draining the colour.
 *
 * Cobalt rather than green: green stays reserved for price change.
 */
export function gradeTone(grade?: string | null, variant: ActionVariant = 'pill'): string {
  const g = (grade ?? '').trim();
  if (!g || g === '—') {
    return variant === 'pill' ? 'text-content-disabled' : 'text-content-disabled';
  }
  if (g[0].toUpperCase() !== 'A') {
    // B / C / D — no colour, no fill.
    return 'text-content-medium';
  }
  if (variant === 'text') return 'text-brand';
  const mod = g.slice(1);
  const fill = mod === '+' ? 'bg-brand/25' : mod === '-' ? 'bg-brand/10' : 'bg-brand/[0.18]';
  return `text-brand ${fill}`;
}

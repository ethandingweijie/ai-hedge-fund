/**
 * Report glossary — one plain sentence per term (owner, 2026-11-27).
 *
 * The single place the report explains its vocabulary to a novice reader.
 * Every "?" tip (components/report/shared/InfoTip) reads from here, on both
 * render paths, so a definition is written once and cannot drift between
 * desktop and mobile. Entries are English source text and double as i18n keys
 * (zh in i18n/zh.ts).
 *
 * Also maps internal engine keys that reached the UI ("archive_news_delta",
 * "holdco_template") to plain labels.
 */

export const GLOSSARY: Record<string, string> = {
  'Market cap': 'What the whole company is worth on the stock market: share price times the number of shares.',
  'TTM': 'Trailing twelve months: the last four reported quarters added together.',
  'Revenue': 'Total sales the company booked, before any costs.',
  'Free cash flow': 'Cash the business generated after paying for running costs and investment (FCF); what is left for dividends, buybacks or paying down debt.',
  'Net margin': 'The share of each unit of sales left as profit after every cost, interest and tax.',
  'P/E': 'Price-to-earnings: the share price divided by one year of profit per share. Higher means investors pay more for each unit of profit.',
  'P/S': 'Price-to-sales: the company\'s market value divided by one year of sales.',
  'P/B': 'Price-to-book: the share price divided by the accounting value of the company\'s net assets per share.',
  'EV / EBITDA': 'Company value including debt (EV) divided by operating profit before depreciation (EBITDA). A common way to compare companies with different debt levels.',
  'Revenue growth': 'How much sales grew compared with a year earlier.',
  'EPS': 'Earnings per share: the company\'s profit divided by the number of shares.',
  'Return on equity': 'Profit as a share of the money shareholders have in the company (ROE). Higher means the company earns more on that money.',
  'ROIC': 'Return on invested capital: profit as a share of all the money (debt and equity) put into the business.',
  'FCF yield': 'Free cash flow divided by market value: the cash return the business generates for each unit you pay for the shares.',
  'Dividend yield': 'Dividends paid over a year as a percentage of the share price.',
  'Net cash': 'Cash minus debt. A negative number means the company owes more than it holds in cash (net debt).',
  '52-week high': 'The highest closing price in the past year.',
  '52-week low': 'The lowest closing price in the past year.',
  'Volume': 'How many shares changed hands in the latest trading day.',
  'Day high': 'The highest price traded today.',
  'Day low': 'The lowest price traded today.',
  // Report vocabulary
  'Target': 'Where we expect the share price to be in 12 months.',
  'Intrinsic value': 'Our estimate of what one share is worth based on the business\'s long-run cash flows, regardless of today\'s market mood.',
  'TSR': 'Total shareholder return: the expected price change plus dividends over the period.',
  'Benchmark': 'The market index the rating is measured against. Its expected return is what an average stock in that market should earn.',
  'Valuation band': 'A long-run value check: compares today\'s price with our intrinsic value only. The rating also weighs the 12-month price path, dividends and rating rules, so the two can differ.',
  'Expected value': 'The value per share averaged across our bear, base and bull cases, weighted by how likely we think each is.',
  'Power-law': 'A 0 to 10 score of how much room the business has to compound (market size, moat, reinvestment). 10 is strongest.',
  'Value trap': 'A stock that looks cheap but stays cheap because the business is shrinking or its profits are not real cash.',
  'Conviction': 'How much the qualitative evidence strengthens (above 1) or weakens (below 1) the position size.',
  'Position': 'The share of a model portfolio we would put in this stock.',
  'Regime': 'Our read of the overall market mood (risk-on means investors are buying riskier assets).',
  'DCF': 'Discounted cash flow: a value built from the cash the business is expected to generate, converted into today\'s money.',
  'WACC': 'The yearly return investors require from the company; used to convert future cash into today\'s money.',
  'SOTP': 'Sum of the parts: each business unit valued on its own, then added up.',
  'Valuation': 'Grade for how cheap the stock is against its value and peers (A is cheapest).',
  'Growth': 'Grade for how fast sales and profits are growing (A is fastest).',
  'Profitability': 'Grade for how much profit and cash the business makes from its sales and capital (A is best).',
  'Momentum': 'Grade for the recent share-price trend and estimate revisions (A is strongest).',
};

/** Label aliases used by the two render paths ("P/E (TTM)", "EPS (TTM)", "Value", "Profit"). */
const ALIASES: Record<string, string> = {
  'P/E (TTM)': 'P/E', 'P/S (TTM)': 'P/S', 'EPS (TTM)': 'EPS', 'Revenue (TTM)': 'Revenue',
  'Value': 'Valuation', 'Profit': 'Profitability', 'Upside': 'Target', '12m PT': 'Target',
  'Blended IV': 'Intrinsic value', 'FCF': 'Free cash flow', 'ROE': 'Return on equity',
};

export const TTM_NOTE = 'TTM = the last twelve months.';

/**
 * The plain sentence(s) for a label, or null when the label needs none. Each
 * part is an i18n key; "(TTM)" labels get the TTM note as a second part so the
 * abbreviation is explained where it appears.
 */
export function glossaryFor(label: string): string[] | null {
  const k = ALIASES[label] ?? label;
  const base = GLOSSARY[k] ?? null;
  if (!base) return null;
  return /\(TTM\)/.test(label) ? [base, TTM_NOTE] : [base];
}

/** Internal engine keys that used to leak into the UI, mapped to plain labels. */
const PLAIN_KEYS: Record<string, string> = {
  // research tiers
  archive_news_delta: 'Earlier research, updated for recent news',
  archive: 'Earlier research reused',
  archive_reuse: 'Earlier research reused',
  fast: 'Quick research',
  full: 'Full research',
  deep: 'Full research',
  fresh: 'New research',
  // SOTP sources
  holdco_template: 'Holding-company template',
  analyst: 'Analyst segment estimates',
  market: 'Market prices of listed stakes',
  filings: 'Company filings',
  all: 'All segments',
};

/** A plain label for an internal key; unknown keys are de-snaked ("some_key" -> "Some key"). */
export function plainKey(key: string | null | undefined): string {
  if (!key) return '';
  const hit = PLAIN_KEYS[key] ?? PLAIN_KEYS[key.toLowerCase()];
  if (hit) return hit;
  const s = key.replace(/[_-]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

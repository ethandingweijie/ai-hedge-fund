/**
 * FinancialStatements
 *
 * Income statement, balance sheet and cash flow as three tabbed statements
 * with a derived YoY growth column beside each level — the shape every
 * analyst note in the archive uses.
 *
 * The payload is built server-side (src/tools/financial_statements.py) from
 * FMP line items and is already profile-aware: a bank arrives with net
 * interest income and total income and no gross-profit row, an S-REIT with
 * gross revenue and net property income. This component renders whatever
 * rows it is given and never assumes an industrial's layout.
 *
 * Growth is rendered monochrome on purpose. Green and red are reserved for
 * price change (see lib/semanticColors); a revenue line moving up is not a
 * price move, so it takes emphasis, not hue.
 */

import { useState } from 'react';
import { Card } from '@/components/ui/card';
import { emphasisTone } from '@/lib/semanticColors';
import { currencySymbol } from '@/lib/utils';

// ── Types (mirror build_financial_statements) ────────────────────────────────

export interface StatementRow {
  key:      string;
  label:    string;
  values:   Record<string, number | null>;
  growth:   Record<string, number | null>;
  emphasis: boolean;
  indent:   number;
}

interface Statement {
  title: string;
  rows:  StatementRow[];
}

/** What the forecast columns are, or why there are none (attach_forecast, server-side). */
export interface StatementForecastMeta {
  status:  'ok' | 'withheld' | 'not_applicable' | 'none';
  reason?: string;
  source?: string;
  reconciliation?: { ok?: boolean | null; assertions?: { id: number; name?: string; ok: boolean }[] };
  override?: { created_at?: string; fields?: string[] };
}

export interface FinancialStatementsPayload {
  layout?:     string;
  currency?:   string;
  periods?:    string[];
  /** Owner, 2026-10-04: FY+1E..FY+5E from the valuation agent's three-statement forecast,
   *  on the same rows as the reported years. `periods` stays the reported years. */
  forecast_periods?: string[];
  forecast?:   StatementForecastMeta;
  statements?: Partial<Record<StatementId, Statement>>;
  /** {FY: reason} — years whose cost lines the provider classified differently from the
   *  company's own history: withheld, and no cost-line growth is computed across them. */
  not_comparable?: Record<string, string>;
}

type StatementId = 'income' | 'balance' | 'cashflow';

const TABS: { id: StatementId; label: string }[] = [
  { id: 'income',   label: 'Income Statement' },
  { id: 'balance',  label: 'Balance Sheet' },
  { id: 'cashflow', label: 'Cash Flow' },
];

// ── Formatting ───────────────────────────────────────────────────────────────

function fmtValue(v: number | null, sym: string): string {
  if (v == null || !isFinite(v)) return '—';
  const abs = Math.abs(v);
  // Per-share and ratio figures arrive small; show them as-is.
  if (abs < 1000) return `${v < 0 ? '−' : ''}${sym}${abs.toFixed(2)}`;
  const sign = v < 0 ? '−' : '';
  if (abs >= 1e12) return `${sign}${sym}${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9)  return `${sign}${sym}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6)  return `${sign}${sym}${(abs / 1e6).toFixed(1)}M`;
  return `${sign}${sym}${(abs / 1e3).toFixed(1)}K`;
}

function fmtGrowth(g: number | null): string {
  if (g == null || !isFinite(g)) return '';
  const pct = g * 100;
  if (Math.abs(pct) < 0.05) return '0.0%';   // a held line: no sign on a rounded zero
  const sign = pct >= 0 ? '+' : '−';
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

function shortPeriod(p: string): string {
  // FY2025 -> '25, FY2026E -> '26E
  return p.replace(/^FY/, "'").replace(/^'(\d{2})(\d{2})(E?)$/, "'$2$3");
}

/** The payload carries a currency CODE (reported_currency, e.g. "SGD"),
 *  whereas lib/utils' currencySymbol resolves a TICKER suffix. Map the code
 *  directly and fall back to the ticker only when no code was reported. */
const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$', SGD: 'S$', HKD: 'HK$', CNY: 'RMB ', RMB: 'RMB ', JPY: 'JP¥', KRW: '₩',
  EUR: '€', GBP: '£', GBp: '£', AUD: 'A$', TWD: 'NT$', INR: '₹',
};

function resolveSymbol(currency: string | undefined, ticker: string): string {
  const code = (currency || '').trim().toUpperCase();
  if (code && CURRENCY_SYMBOLS[code]) return CURRENCY_SYMBOLS[code];
  return currencySymbol(ticker);
}

// ── Component ────────────────────────────────────────────────────────────────

export function FinancialStatements({
  statements,
  ticker,
}: {
  statements?: FinancialStatementsPayload;
  ticker: string;
}) {
  const available = TABS.filter(t => statements?.statements?.[t.id]?.rows?.length);
  const [tab, setTab] = useState<StatementId>(available[0]?.id ?? 'income');

  const periods = statements?.periods ?? [];
  if (!available.length || !periods.length) return null;

  const active = available.some(t => t.id === tab) ? tab : available[0].id;
  const stmt = statements!.statements![active]!;
  const sym = resolveSymbol(statements?.currency, ticker);

  // Forecast years ride on the reported rows; a column is shown only when at least one
  // row of the active statement carries a figure for it.
  const forecast = (statements?.forecast_periods ?? []).filter(p =>
    stmt.rows.some(r => r.values?.[p] != null));
  const fcSet = new Set(forecast);
  const columns = [...periods, ...forecast];
  const meta = statements?.forecast;
  const suite = meta?.reconciliation;
  const failed = (suite?.assertions ?? []).filter(a => !a.ok);
  const notComparable = statements?.not_comparable ?? {};

  return (
    <Card className="p-0 overflow-hidden">
      {/* Statement selector */}
      <div className="flex items-center gap-1 border-b border-border px-3 pt-3">
        {available.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={[
              'px-3 py-2 text-xs font-medium rounded-t transition-colors',
              t.id === active
                ? 'border-b-2 border-foreground text-foreground'
                : `border-b-2 border-transparent ${emphasisTone('muted')} hover:text-foreground`,
            ].join(' ')}
          >
            {t.label}
          </button>
        ))}
        {statements?.layout && statements.layout !== 'standard' && (
          <span className={`ml-auto pb-2 text-xs uppercase tracking-wide ${emphasisTone('ghost')}`}>
            {statements.layout} layout
          </span>
        )}
      </div>

      {/* The table scrolls inside its own container so the page never does */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm tabular-nums">
          <thead>
            {forecast.length > 0 && (
              <tr>
                <th className="sticky left-0 z-10 bg-background" />
                <th colSpan={periods.length}
                  className={`text-right font-medium px-3 pt-2 text-xs uppercase tracking-wide ${emphasisTone('ghost')}`}>
                  Reported
                </th>
                <th colSpan={forecast.length}
                  className={`text-right font-medium px-3 pt-2 text-xs uppercase tracking-wide border-l border-border ${emphasisTone('ghost')}`}>
                  Forecast (valuation agent)
                </th>
              </tr>
            )}
            <tr className="border-b border-border">
              <th className={`sticky left-0 z-10 bg-background after:pointer-events-none after:absolute after:inset-y-0 after:-right-4 after:w-4 after:bg-gradient-to-r after:from-background after:to-transparent text-left font-medium px-3 py-2 ${emphasisTone('muted')}`}>
                {stmt.title}
              </th>
              {columns.map((p, i) => (
                <th
                  key={p}
                  className={[
                    'text-right font-medium px-3 py-2 whitespace-nowrap',
                    emphasisTone('muted'),
                    fcSet.has(p) ? 'italic bg-muted/30' : '',
                    i === periods.length && forecast.length ? 'border-l border-border' : '',
                  ].join(' ')}
                >
                  {shortPeriod(p)}{notComparable[p] ? '*' : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stmt.rows.map(row => (
              <tr
                key={row.key}
                className={row.emphasis ? 'border-t border-border/60' : ''}
              >
                <td
                  className={[
                    'sticky left-0 z-10 bg-background px-3 py-1.5 whitespace-nowrap after:pointer-events-none after:absolute after:inset-y-0 after:-right-4 after:w-4 after:bg-gradient-to-r after:from-background after:to-transparent',
                    row.indent ? 'pl-7' : '',
                    row.emphasis ? 'font-medium text-foreground' : emphasisTone('medium'),
                  ].join(' ')}
                >
                  <span title={row.label} className="block max-w-[9.5rem] truncate sm:max-w-none">{row.label}</span>
                </td>
                {columns.map((p, i) => {
                  const g = row.growth?.[p] ?? null;
                  return (
                    <td key={p}
                      className={[
                        'px-3 py-1.5 text-right whitespace-nowrap',
                        fcSet.has(p) ? 'bg-muted/30' : '',
                        i === periods.length && forecast.length ? 'border-l border-border' : '',
                      ].join(' ')}>
                      <span className={row.emphasis ? 'font-medium' : ''}>
                        {fmtValue(row.values?.[p] ?? null, sym)}
                      </span>
                      {g != null && (
                        <span className={`ml-2 text-xs ${emphasisTone('ghost')}`}>
                          {fmtGrowth(g)}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className={`px-3 py-2 text-xs space-y-1 ${emphasisTone('ghost')}`}>
        <p>
          Growth is year-on-year, derived from the reported series. Blank where
          the prior year is zero or the sign flips.
        </p>
        {Object.entries(notComparable).map(([p, why]) => (
          <p key={p}>
            {shortPeriod(p)}* not comparable: {why}. Cost lines withheld; operating income is the reported figure.
          </p>
        ))}
        {forecast.length > 0 && (
          <p>
            {shortPeriod(forecast[0])}–{shortPeriod(forecast[forecast.length - 1])} are{' '}
            {meta?.source ?? "the valuation agent's three-statement forecast on its guidance-derived estimates"};
            a line the model does not build stays blank.{' '}
            {suite && (suite.ok
              ? `Reconciliation suite: all ${suite.assertions?.length ?? 5} checks pass in every forecast year.`
              : `Reconciliation suite: FAILED${failed.length ? ` (${failed.map(a => a.name ?? `check ${a.id}`).join('; ')})` : ''}.`)}
            {meta?.override?.fields?.length
              ? ` Built on your saved estimates (${meta.override.fields.join(', ')}).`
              : ''}
          </p>
        )}
        {forecast.length === 0 && meta?.status === 'withheld' && (
          <p>Forecast columns withheld: {meta.reason}</p>
        )}
        {forecast.length === 0 && meta?.status === 'not_applicable' && (
          <p>Forecast: {meta.reason}</p>
        )}
      </div>
    </Card>
  );
}

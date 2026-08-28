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

export interface FinancialStatementsPayload {
  layout?:     string;
  currency?:   string;
  periods?:    string[];
  statements?: Partial<Record<StatementId, Statement>>;
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
  const sign = pct >= 0 ? '+' : '−';
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

function shortPeriod(p: string): string {
  return p.replace(/^FY/, "'").replace(/^'(\d{2})(\d{2})$/, "'$2");
}

/** The payload carries a currency CODE (reported_currency, e.g. "SGD"),
 *  whereas lib/utils' currencySymbol resolves a TICKER suffix. Map the code
 *  directly and fall back to the ticker only when no code was reported. */
const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$', SGD: 'S$', HKD: 'HK$', CNY: '¥', RMB: '¥', JPY: '¥',
  KRW: '₩', EUR: '€', GBP: '£', GBp: '£', AUD: 'A$', TWD: 'NT$', INR: '₹',
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
          <span className={`ml-auto pb-2 text-[10px] uppercase tracking-wide ${emphasisTone('ghost')}`}>
            {statements.layout} layout
          </span>
        )}
      </div>

      {/* The table scrolls inside its own container so the page never does */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm tabular-nums">
          <thead>
            <tr className="border-b border-border">
              <th className={`text-left font-medium px-3 py-2 ${emphasisTone('muted')}`}>
                {stmt.title}
              </th>
              {periods.map(p => (
                <th
                  key={p}
                  className={`text-right font-medium px-3 py-2 whitespace-nowrap ${emphasisTone('muted')}`}
                >
                  {shortPeriod(p)}
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
                    'px-3 py-1.5 whitespace-nowrap',
                    row.indent ? 'pl-7' : '',
                    row.emphasis ? 'font-medium text-foreground' : emphasisTone('medium'),
                  ].join(' ')}
                >
                  {row.label}
                </td>
                {periods.map(p => {
                  const g = row.growth?.[p] ?? null;
                  return (
                    <td key={p} className="px-3 py-1.5 text-right whitespace-nowrap">
                      <span className={row.emphasis ? 'font-medium' : ''}>
                        {fmtValue(row.values?.[p] ?? null, sym)}
                      </span>
                      {g != null && (
                        <span className={`ml-2 text-[11px] ${emphasisTone('ghost')}`}>
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

      <div className={`px-3 py-2 text-[11px] ${emphasisTone('ghost')}`}>
        Growth is year-on-year, derived from the reported series. Blank where
        the prior year is zero or the sign flips.
      </div>
    </Card>
  );
}

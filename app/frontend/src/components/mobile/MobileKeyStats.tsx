import { currencySymbol } from '@/lib/utils';

interface MobileKeyStatsProps {
  ticker: string;
  metrics?: {
    market_cap?:       number;
    revenue?:          number;
    free_cash_flow?:   number;
    net_margin?:       number;
    pe_ratio?:         number;
    revenue_growth?:   number;
    ev_to_ebitda?:     number;
    return_on_equity?: number;
  };
}

function makeFmtLarge(sym: string) {
  return (v: number | undefined): string => {
    if (v == null) return '—';
    const abs = Math.abs(v);
    if (abs >= 1e12) return `${sym}${(v / 1e12).toFixed(1)}T`;
    if (abs >= 1e9)  return `${sym}${(v / 1e9).toFixed(1)}B`;
    if (abs >= 1e6)  return `${sym}${(v / 1e6).toFixed(1)}M`;
    return `${sym}${v.toLocaleString()}`;
  };
}

function fmtPct(v: number | undefined): string {
  if (v == null) return '—';
  const pct = v * 100;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
}

function fmtMultiple(v: number | undefined): string {
  if (v == null) return '—';
  return `${v.toFixed(1)}x`;
}

type StatDef = {
  key: string;
  label: string;
  format: (v: number | undefined, sym: string) => string;
  signed?: boolean;
};

const STATS: StatDef[] = [
  { key: 'market_cap',       label: 'Mkt Cap',    format: (v, sym) => makeFmtLarge(sym)(v) },
  { key: 'revenue',          label: 'Rev TTM',     format: (v, sym) => makeFmtLarge(sym)(v) },
  { key: 'free_cash_flow',   label: 'FCF',         format: (v, sym) => makeFmtLarge(sym)(v) },
  { key: 'net_margin',       label: 'Net Margin',  format: (v) => fmtPct(v), signed: true },
  { key: 'pe_ratio',         label: 'P/E',         format: (v) => fmtMultiple(v) },
  { key: 'revenue_growth',   label: 'Rev Growth',  format: (v) => fmtPct(v), signed: true },
  { key: 'ev_to_ebitda',     label: 'EV/EBITDA',   format: (v) => fmtMultiple(v) },
  { key: 'return_on_equity', label: 'ROE',         format: (v) => fmtPct(v), signed: true },
];

export function MobileKeyStats({ ticker, metrics }: MobileKeyStatsProps) {
  const sym = currencySymbol(ticker);

  if (!metrics) return null;

  return (
    <div className="px-4 py-2">
      {/* 4-column × 2-row grid — fits all 8 stats without overflow */}
      <div className="grid grid-cols-4 gap-2">
        {STATS.map(({ key, label, format, signed }) => {
          const val = metrics[key as keyof typeof metrics];
          const formatted = format(val, sym);
          const colorClass = !signed || val == null
            ? 'text-foreground'
            : val >= 0 ? 'text-green-500' : 'text-red-500';

          return (
            <div
              key={key}
              className="bg-card border border-border rounded-lg px-2 py-1.5 text-center"
            >
              <p className="text-[8px] uppercase tracking-wider text-muted-foreground leading-none mb-0.5">
                {label}
              </p>
              <p className={`text-[13px] font-bold tabular-nums leading-tight ${colorClass}`}>
                {formatted}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

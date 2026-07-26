import { Link } from 'react-router-dom';
import { MessageSquare } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { useCompanyProfile } from '@/hooks/use-company-name';
import type { PortfolioDecision, MacroRegime, VgpmResult } from '@/lib/reportTypes';
import { gradeColorClass, formatSector } from '@/lib/gradeColors';
import { currencySymbol } from '@/lib/utils';

interface ReportHeaderProps {
  ticker: string;
  runAt: string;
  modelName?: string;
  decision?: PortfolioDecision;
  regime?: MacroRegime;
  currentPrice?: number;
  sector?: string;
  subSector?: string;
  vgpm?: VgpmResult;
}

const actionColor: Record<string, string> = {
  BUY:   'bg-green-600 text-white',
  SELL:  'bg-red-600 text-white',
  SHORT: 'bg-orange-600 text-white',
  COVER: 'bg-blue-600 text-white',
  HOLD:  'bg-yellow-600 text-white',
};

const VGPM_DIMS = [
  { key: 'valuation',     label: 'V' },
  { key: 'growth',        label: 'G' },
  { key: 'profitability', label: 'P' },
  { key: 'momentum',      label: 'M' },
] as const;

export function ReportHeader({ ticker, runAt, modelName, decision, regime, currentPrice, sector, subSector, vgpm }: ReportHeaderProps) {
  const action = decision?.action ?? '—';
  const colorClass = actionColor[action] ?? 'bg-muted text-muted-foreground';
  const profile = useCompanyProfile(ticker);
  const companyName = profile?.name ?? null;

  const displaySector    = formatSector(sector    || profile?.sector   || null);
  const displaySubSector = formatSector(subSector || profile?.industry || null);

  return (
    <Card className="p-6 flex flex-col min-w-0">

      {/* ── Ticker / action / run info ──
          Own row now (not squeezed beside the stats), so it never competes
          for width with anything else — the stats and VGPM rows below are
          full-card-width CSS Grids instead, which stay a stable 4-column
          layout at any card width instead of flex-wrapping unpredictably
          between a single row and an uneven 2x2 depending on how much room
          the sidebar leaves (that used to make the card visibly "jump"
          between layouts as the sidebar toggled). */}
      <div className="min-w-0">
        {companyName && (
          <p className="text-sm text-muted-foreground font-medium leading-none mb-1">{companyName}</p>
        )}
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-bold tracking-tight">{ticker}</h1>
          <span className={`px-3 py-1 rounded-full text-sm font-semibold ${colorClass}`}>{action}</span>
          <Link
            to={`/discuss/${ticker}`}
            title={`Discuss ${ticker}`}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-brand text-white hover:bg-brand/90 transition-colors"
          >
            <MessageSquare size={13} />
            Discuss
          </Link>
        </div>
        <p className="text-muted-foreground text-xs mt-1">
          {runAt && !isNaN(new Date(runAt).getTime()) ? `Run ${new Date(runAt).toLocaleString()} · ` : ''}{modelName ?? 'N/A'}
        </p>
      </div>

      {/* ── Position / Target / Current / Regime ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 pt-4 border-t border-border/60">
        {decision?.position_size_pct != null && (
          <div>
            <p className="text-xs text-muted-foreground uppercase tracking-wider">Position</p>
            <p className="text-xl font-bold">{(decision.position_size_pct * 100).toFixed(2)}%</p>
          </div>
        )}
        {decision?.price_target != null && decision.price_target > 0 && (
          <div>
            <p className="text-xs text-muted-foreground uppercase tracking-wider">Target</p>
            <p className="text-xl font-bold">{currencySymbol(ticker)}{decision.price_target.toFixed(2)}</p>
          </div>
        )}
        {currentPrice != null && (
          <div>
            <p className="text-xs text-muted-foreground uppercase tracking-wider">Current</p>
            <p className="text-xl font-bold">{currencySymbol(ticker)}{currentPrice.toFixed(2)}</p>
          </div>
        )}
        {regime && (
          <div>
            <p className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Regime</p>
            <div className="flex flex-wrap gap-1">
              <span className="inline-flex items-center px-2 py-0.5 rounded border border-border text-xs text-foreground/80">{regime.risk_appetite}</span>
              <span className="inline-flex items-center px-2 py-0.5 rounded border border-border text-xs text-foreground/80">{regime.volatility_regime} vol</span>
            </div>
          </div>
        )}
      </div>

      {/* ── VGPM ── */}
      {vgpm && (
        <div className="grid grid-cols-4 gap-3 mt-4 pt-4 border-t border-border/60">
          {VGPM_DIMS.map(({ key }) => {
            const dim = vgpm[key];
            if (!dim) return (
              <div key={key} />
            );
            const fullLabel = key.charAt(0).toUpperCase() + key.slice(1);
            const tooltip = [
              `${fullLabel}: ${dim.score}/100`,
              ...(dim.subs ?? []),
            ].join('\n');
            return (
              <div key={key} className="flex flex-col items-center gap-1">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground whitespace-nowrap">{fullLabel}</span>
                <span
                  title={tooltip}
                  className={`text-xl font-bold px-3 py-1 rounded-lg cursor-help ${gradeColorClass(dim.grade)}`}
                >
                  {dim.grade}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Rationale ── */}
      {/* Cap line length to a comfortable reading measure (~72ch). `ch` units
          track the element's font-size, so this stays optimal even when the
          desktop root font scales up on large screens — without it the thesis
          stretched the full card width and lines got hard to track. */}
      {decision?.rationale && (
        <p className="mt-4 text-lg text-muted-foreground pt-4 leading-relaxed max-w-[72ch]">
          {decision.rationale}
        </p>
      )}

      {/* ── Sector ── */}
      {(displaySector || displaySubSector) && (
        <div className="mt-4 pt-3 border-t flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mr-1">Sector</span>
          {displaySector && (
            <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-primary/10 text-primary border border-primary/20">
              {displaySector}
            </span>
          )}
          {displaySector && displaySubSector && displaySubSector !== displaySector && (
            <span className="text-muted-foreground/40 text-sm">›</span>
          )}
          {displaySubSector && displaySubSector !== displaySector && (
            <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-muted text-foreground/80 border border-border">
              {displaySubSector}
            </span>
          )}
        </div>
      )}
    </Card>
  );
}

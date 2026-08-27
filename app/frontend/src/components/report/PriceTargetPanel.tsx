/**
 * PriceTargetPanel — the 12-Month Price Target card.
 *
 * Standardised 2026-07 to match the mobile view exactly (same component,
 * used by both V2ReportView and the desktop pages) instead of desktop
 * carrying its own separate, much more elaborate dual-blend-table
 * treatment. Shows the headline 12-month price target (+ Wall St.
 * consensus sanity line) and a probability-weighted Bear/Base/Bull table
 * with both the 12m target and the long-term DCF intrinsic value per
 * scenario — the DCF IV column is what previously lived in a whole
 * separate "Long-term Intrinsic Value" table; folding it into this one
 * table carries the same information with far less chrome.
 */

import { Card } from '@/components/ui/card';
import { currencySymbol } from '@/lib/utils';
import type {
  DcfRange,
  ScenarioAnalysis,
  PortfolioDecision,
} from '@/lib/reportTypes';

interface PriceTargetPanelProps {
  dcfRange?: DcfRange;
  scenario?: ScenarioAnalysis;
  decision?: PortfolioDecision;
  ticker: string;
}

export function PriceTargetPanel({ dcfRange, scenario, decision, ticker }: PriceTargetPanelProps) {
  const sym = currencySymbol(ticker);

  const target = scenario?.['12m_price_target'] ?? decision?.price_target ?? null;
  const current = scenario?.current_price ?? null;
  const upside = (target != null && current != null && current > 0)
    ? ((target - current) / current) * 100
    : (scenario?.upside_pct ?? null);

  const bullIV = dcfRange?.bull?.intrinsic_value ?? scenario?.bull?.fair_value ?? null;
  const baseIV = dcfRange?.base?.intrinsic_value ?? scenario?.base?.fair_value ?? null;
  const bearIV = dcfRange?.bear?.intrinsic_value ?? scenario?.bear?.fair_value ?? null;
  const bearDelta = (bearIV != null && current != null && current > 0) ? ((bearIV - current) / current) * 100 : null;
  const baseDelta = (baseIV != null && current != null && current > 0) ? ((baseIV - current) / current) * 100 : null;
  const wacc = dcfRange?.wacc ?? null;

  const has12mTargets = dcfRange?.['12m_targets'] ?? {};
  const bull12m = has12mTargets.bull ?? bullIV;
  const base12m = has12mTargets.base ?? target ?? baseIV;
  const bear12m = has12mTargets.bear ?? bearIV;

  // Wall Street analyst consensus PT (FMP /price-target-consensus, persisted
  // in dcf_range.consensus_pt by dcf_agent) — small sanity line under the
  // headline so the reader sees model vs market. null for HK/SG or fetch failure.
  const consensusPt = dcfRange?.consensus_pt?.consensus ?? null;
  const consensusDelta = (consensusPt != null && target != null && target > 0)
    ? ((consensusPt - target) / target) * 100
    : null;

  const probBull = scenario?.bull?.probability ?? 0.25;
  const probBase = scenario?.base?.probability ?? 0.50;
  const probBear = scenario?.bear?.probability ?? 0.25;

  const hasScenarioTable = !!(scenario || dcfRange);

  if (target == null && !hasScenarioTable) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">Price target data unavailable for {ticker}.</p>
      </Card>
    );
  }

  return (
    <Card className="p-5">
      {target != null && (
        <div className="text-center">
          <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
            12-Month Price Target — {ticker}
          </div>
          <div className="text-[34px] font-semibold tracking-tight text-foreground tabular-nums mt-1 leading-none">
            {sym}{target.toFixed(2)}
          </div>
          {upside != null && (
            <div className={`mt-2 text-[14px] font-medium tabular-nums ${upside >= 0 ? 'text-gain' : 'text-loss'}`}>
              {upside >= 0 ? '+' : ''}{upside.toFixed(1)}% upside
            </div>
          )}
          {current != null && (
            <div className="text-[11px] text-muted-foreground">
              vs current {sym}{current.toFixed(2)}
            </div>
          )}
          {consensusPt != null && (
            <div className="mt-1.5 text-[11px] text-muted-foreground">
              Wall St. consensus{' '}
              <span className="font-semibold text-foreground/80 tabular-nums">
                {sym}{consensusPt.toFixed(2)}
              </span>
              {consensusDelta != null && (
                <span className={`ml-1 tabular-nums ${
                  Math.abs(consensusDelta) < 5
                    ? 'text-muted-foreground/70'
                    : consensusDelta > 0
                      ? 'text-brand'
                      : 'text-content-high'
                }`}>
                  ({consensusDelta >= 0 ? '+' : ''}{consensusDelta.toFixed(1)}% vs model)
                </span>
              )}
            </div>
          )}
          {baseDelta != null && bearDelta != null && (
            <p className="text-[11px] text-muted-foreground leading-relaxed mt-2.5">
              Base case implies {baseDelta >= 0 ? '+' : ''}{baseDelta.toFixed(0)}% upside; bear-case downside is {Math.abs(bearDelta).toFixed(0)}%.
            </p>
          )}
        </div>
      )}

      {hasScenarioTable && (
        <div className={target != null ? 'mt-5 pt-4 border-t border-border/60' : ''}>
          <div className="flex items-center justify-between mb-2.5">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">
              Scenario Probabilities
            </span>
            {wacc != null && <span className="text-[10px] tabular-nums text-muted-foreground/70">WACC {(wacc * 100).toFixed(1)}%</span>}
          </div>
          <div className="flex items-center justify-end gap-2 px-1 pb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
            <span className="w-[60px] text-right">12M Target</span>
            <span className="w-[56px] text-right">DCF IV</span>
          </div>
          {[
            { prob: probBear, name: 'Bear', target12m: bear12m, iv: bearIV, color: 'rose' as const },
            { prob: probBase, name: 'Base', target12m: base12m, iv: baseIV, color: 'neutral' as const },
            { prob: probBull, name: 'Bull', target12m: bull12m, iv: bullIV, color: 'brand' as const },
          ].map((r, i) => (
            <div key={r.name} className={`flex items-center gap-2 py-2 ${i > 0 ? 'border-t border-border/60' : ''}`}>
              <span className="w-[34px] text-[11.5px] font-semibold text-foreground/80 tabular-nums">
                {Math.round((r.prob ?? 0) * 100)}%
              </span>
              <div className="w-[60px] h-1.5 rounded-full bg-muted overflow-hidden">
                <div
                  style={{ width: `${Math.min(100, (r.prob ?? 0) * 200)}%` }}
                  className={`h-full ${r.color === 'rose' ? 'bg-surface-2' : r.color === 'neutral' ? 'bg-foreground/35' : 'bg-brand'}`}
                />
              </div>
              <span className="text-[12.5px] font-semibold text-foreground min-w-[40px]">{r.name}</span>
              <span className="ml-auto w-[60px] text-right text-[12px] font-semibold tabular-nums text-foreground">
                {r.target12m != null ? `${sym}${r.target12m.toFixed(2)}` : '—'}
              </span>
              <span className="w-[56px] text-right text-[11px] tabular-nums text-muted-foreground">
                {r.iv != null ? `${sym}${r.iv.toFixed(2)}` : '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

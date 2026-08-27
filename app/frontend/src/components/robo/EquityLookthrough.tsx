/**
 * EquityLookthrough.tsx
 * =====================
 * What COMPANIES the recommended fund plan actually owns.
 *
 * The holdings table answers "which funds do I buy". This answers "what do I
 * end up owning", by resolving each equity fund to its constituents and
 * aggregating by company — so a name held through two different funds shows
 * once, at its combined weight, with the funds that carry it.
 *
 * Three numbers are shown together on purpose. `resolved` is what could be
 * attributed to named companies; `uncovered` is equity-fund weight whose
 * constituents aren't stored (a fund's holdings list is truncated at 50, so
 * a 3,500-holding total-market fund is only partly visible); `non-equity` is
 * bond and commodity funds, which have no company holdings to look through.
 * They always sum to the plan. Every position is therefore a FLOOR — the
 * uncovered tail can only add to a weight, never subtract — and saying so is
 * the point of surfacing the split rather than just listing companies.
 *
 * Monochrome throughout: green/red is reserved for price change.
 */
import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { RoboEquityLookthrough } from '@/lib/api';

interface Props {
  lookthrough?: RoboEquityLookthrough;
  totalInvestment: number;
}

const money = (v: number) =>
  v >= 1000 ? `$${Math.round(v).toLocaleString()}` : `$${v.toFixed(0)}`;

export function EquityLookthrough({ lookthrough, totalInvestment }: Props) {
  const [showAll, setShowAll] = useState(false);

  // Absent on portfolios generated before this shipped, and on plans with no
  // equity funds at all — say so rather than rendering an empty shell.
  if (!lookthrough || lookthrough.positions.length === 0) {
    return (
      <div className="p-6 text-center text-sm text-muted-foreground rounded-lg border border-border bg-card">
        No equity look-through available for this plan.
      </div>
    );
  }

  const { positions, position_count, resolved_pct, uncovered_pct,
          non_equity_pct, top_concentration_pct, coverage } = lookthrough;
  const shown = showAll ? positions : positions.slice(0, 10);

  // Funds whose constituent list is materially incomplete — worth naming, so
  // a thin fund isn't mistaken for a small one.
  const thin = Object.entries(coverage)
    .filter(([, pct]) => pct < 90)
    .sort((a, b) => a[1] - b[1]);

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-[13px] font-semibold text-foreground">
          Equity distribution
        </h3>
        <p className="text-[11px] text-muted-foreground mt-0.5">
          The companies your funds hold, aggregated across the plan.
        </p>
      </div>

      {/* Accounting strip — resolved / uncovered / non-equity sum to the plan */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-border">
        {[
          { label: 'Companies', value: String(position_count) },
          { label: 'Resolved', value: `${resolved_pct.toFixed(1)}%` },
          { label: 'Top 10', value: `${top_concentration_pct.toFixed(1)}%` },
          {
            label: 'Not looked through',
            value: `${(uncovered_pct + non_equity_pct).toFixed(1)}%`,
            hint: `${non_equity_pct.toFixed(1)}% bonds/commodities · ${uncovered_pct.toFixed(1)}% beyond stored holdings`,
          },
        ].map((s) => (
          <div key={s.label} className="bg-card px-4 py-3">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {s.label}
            </div>
            <div className="text-[15px] font-semibold text-foreground tabular-nums mt-0.5">
              {s.value}
            </div>
            {s.hint && (
              <div className="text-[10px] text-muted-foreground/80 mt-0.5">{s.hint}</div>
            )}
          </div>
        ))}
      </div>

      {/* Desktop */}
      <div className="hidden md:block overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Company</TableHead>
              <TableHead>Name</TableHead>
              <TableHead className="text-right">% of plan</TableHead>
              <TableHead className="text-right">Value</TableHead>
              <TableHead>Held via</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((p) => (
              <TableRow key={p.symbol}>
                <TableCell className="font-medium text-foreground">{p.symbol}</TableCell>
                <TableCell className="text-muted-foreground max-w-[280px] truncate">
                  {p.name}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {p.allocationPercent.toFixed(2)}%
                </TableCell>
                <TableCell className="text-right tabular-nums">{money(p.amount)}</TableCell>
                <TableCell className="text-muted-foreground text-[12px]">
                  {p.viaFunds.map((v) => v.ticker).join(', ')}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Mobile */}
      <div className="md:hidden divide-y divide-border">
        {shown.map((p) => (
          <div key={p.symbol} className="px-4 py-3">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-[13px] font-medium text-foreground">{p.symbol}</span>
              <span className="text-[13px] tabular-nums text-foreground">
                {p.allocationPercent.toFixed(2)}%
              </span>
            </div>
            <div className="flex items-baseline justify-between gap-2 mt-0.5">
              <span className="text-[11px] text-muted-foreground truncate">{p.name}</span>
              <span className="text-[11px] tabular-nums text-muted-foreground">
                {money(p.amount)}
              </span>
            </div>
            <div className="text-[10px] text-muted-foreground/80 mt-1">
              via {p.viaFunds.map((v) => v.ticker).join(', ')}
            </div>
          </div>
        ))}
      </div>

      {positions.length > 10 && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="w-full px-4 py-2.5 border-t border-border text-[12px] text-muted-foreground hover:text-foreground flex items-center justify-center gap-1"
        >
          {showAll ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          {showAll ? 'Show top 10' : `Show all ${positions.length}`}
        </button>
      )}

      {thin.length > 0 && (
        <div className="px-4 py-2.5 border-t border-border text-[10px] text-muted-foreground/80">
          Partial constituent data:{' '}
          {thin.map(([t, pct]) => `${t} (${pct.toFixed(0)}%)`).join(', ')}
          {' '}— weights for these are floors.
        </div>
      )}

      {totalInvestment > 0 && (
        <div className="px-4 py-2.5 border-t border-border text-[10px] text-muted-foreground/80">
          Values shown against a {money(totalInvestment)} plan.
        </div>
      )}
    </div>
  );
}

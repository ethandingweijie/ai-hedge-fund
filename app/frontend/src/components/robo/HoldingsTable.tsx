/**
 * HoldingsTable.tsx
 * ==================
 * Recommended-portfolio holdings list — desktop table (shadcn Table,
 * matching WatchlistPage.tsx's structure) + mobile stacked cards. In ETF
 * mode, a row with fetched top-holdings data can expand to show what's
 * actually inside the fund (see etf_metadata_service.py — this degrades to
 * no expand affordance when FMP's holdings endpoint has no data, e.g. under
 * a restricted API plan tier, rather than showing an empty/broken expand).
 */
import { Fragment, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import type { RoboHolding } from '@/lib/api';

interface HoldingsTableProps {
  items: RoboHolding[];
  mode: 'etf' | 'stocks';
}

export function HoldingsTable({ items, mode }: HoldingsTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const isEtf = mode === 'etf';

  if (items.length === 0) {
    return (
      <div className="p-8 text-center text-sm text-muted-foreground rounded-lg border border-border bg-card">
        No holdings generated for this mode.
      </div>
    );
  }

  return (
    <>
      {/* Desktop table */}
      <div className="hidden md:block rounded-lg border border-border overflow-x-auto bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Ticker</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>{isEtf ? 'Category' : 'Sector'}</TableHead>
              <TableHead className="text-right">Allocation</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead className="text-right">Shares</TableHead>
              <TableHead className="text-right">Price</TableHead>
              {isEtf && <TableHead className="text-right">Expense</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => {
              const hasHoldings = isEtf && !!item.topHoldings?.length;
              return (
                <Fragment key={item.ticker}>
                  <TableRow
                    className={hasHoldings ? 'cursor-pointer hover:bg-muted/40' : ''}
                    onClick={() => hasHoldings && setExpanded(expanded === item.ticker ? null : item.ticker)}
                  >
                    <TableCell className="font-mono font-bold text-sm">
                      <span className="flex items-center gap-1">
                        {hasHoldings ? (
                          expanded === item.ticker
                            ? <ChevronDown size={13} className="text-muted-foreground shrink-0" />
                            : <ChevronRight size={13} className="text-muted-foreground shrink-0" />
                        ) : null}
                        {item.ticker}
                      </span>
                    </TableCell>
                    <TableCell className="text-sm max-w-[220px] truncate text-muted-foreground">{item.name}</TableCell>
                    <TableCell className="text-sm">
                      {isEtf && item.category ? (
                        <span className="inline-flex items-center rounded-md border border-brand/25 bg-brand/10 px-1.5 py-0.5 text-[11px] font-semibold text-brand capitalize">
                          {item.category}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">{item.sector ?? '—'}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm">{item.allocationPercent.toFixed(1)}%</TableCell>
                    <TableCell className="text-right font-mono text-sm">
                      ${item.amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm">{item.shares.toLocaleString()}</TableCell>
                    <TableCell className="text-right font-mono text-sm">${item.price.toFixed(2)}</TableCell>
                    {isEtf && (
                      <TableCell className="text-right font-mono text-sm">
                        {item.expenseRatio != null ? `${item.expenseRatio.toFixed(2)}%` : '—'}
                      </TableCell>
                    )}
                  </TableRow>
                  {expanded === item.ticker && hasHoldings && (
                    <TableRow>
                      <TableCell colSpan={isEtf ? 8 : 7} className="bg-muted/30 py-2.5">
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-2">
                          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground/70">
                            Top holdings
                          </span>
                          {item.topHoldings!.map((h, i) => (
                            <span key={i} className="text-[12px] text-foreground">
                              {h.asset} <span className="text-muted-foreground">{h.weightPercentage.toFixed(1)}%</span>
                            </span>
                          ))}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Mobile cards */}
      <div className="md:hidden space-y-2">
        {items.map((item) => {
          const hasHoldings = isEtf && !!item.topHoldings?.length;
          return (
            <div key={item.ticker} className="rounded-lg border border-border bg-card p-3">
              <button
                type="button"
                className="w-full text-left"
                onClick={() => hasHoldings && setExpanded(expanded === item.ticker ? null : item.ticker)}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="font-mono font-bold text-[14px] text-foreground flex items-center gap-1">
                      {hasHoldings ? (
                        expanded === item.ticker
                          ? <ChevronDown size={12} className="text-muted-foreground shrink-0" />
                          : <ChevronRight size={12} className="text-muted-foreground shrink-0" />
                      ) : null}
                      {item.ticker}
                    </div>
                    <div className="text-[11px] text-muted-foreground truncate">{item.name}</div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="font-mono font-semibold text-[14px] text-foreground">{item.allocationPercent.toFixed(1)}%</div>
                    <div className="text-[11px] text-muted-foreground">
                      ${item.amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    </div>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1.5 text-[11px] text-muted-foreground">
                  <span>{item.shares.toLocaleString()} sh @ ${item.price.toFixed(2)}</span>
                  {isEtf && item.expenseRatio != null && <span>Exp: {item.expenseRatio.toFixed(2)}%</span>}
                  {item.sector && <span className="truncate">{item.sector}</span>}
                </div>
              </button>
              {expanded === item.ticker && hasHoldings && (
                <div className="mt-2 pt-2 border-t border-border/60 flex flex-wrap gap-x-2 gap-y-1">
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/70 w-full">
                    Top holdings
                  </span>
                  {item.topHoldings!.map((h, i) => (
                    <span key={i} className="text-[11px] text-foreground">
                      {h.asset} <span className="text-muted-foreground">{h.weightPercentage.toFixed(1)}%</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}

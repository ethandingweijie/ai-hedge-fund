import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { getWatchlist, addToWatchlist, removeFromWatchlist, searchCompanies, getScreenerPrices } from '@/lib/api';
import type { WatchlistItem } from '@/lib/reportTypes';
import type { CompanySearchResult } from '@/lib/api';
import { getProfile } from '@/lib/tier';
import { gradeColorClass } from '@/lib/gradeColors';
import { AgentOrbIcon } from '@/components/report/AgentOrbIcon';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { SwipeableCard } from '@/components/mobile/SwipeableCard';
import { GradeChip } from '@/components/v2/shared';
import { PageContainer } from '@/components/layout/PageContainer';
import { TabHero } from '@/components/layout/TabHero';

// ── Grade pill ────────────────────────────────────────────────────────────────
function GradePill({ grade }: { grade?: string }) {
  const clean = grade?.replace(/^~/, '');
  if (!clean || clean === '—') return <span className="text-muted-foreground text-xs">—</span>;
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-sm font-bold ${gradeColorClass(clean)}`}>
      {clean}
    </span>
  );
}

const VGPM_DIMS = ['valuation', 'growth', 'profitability', 'momentum'] as const;

export function WatchlistPage() {
  const navigate = useNavigate();
  const { mode } = useLayoutMode();
  const [items, setItems]           = useState<WatchlistItem[]>([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState<string | null>(null);
  const [addInput, setAddInput]     = useState('');
  const [adding, setAdding]         = useState(false);
  const [addError, setAddError]     = useState<string | null>(null);
  const [removing, setRemoving]     = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<CompanySearchResult[]>([]);
  const [showSugg, setShowSugg]     = useState(false);
  const [suggLoading, setSuggLoading] = useState(false);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrapperRef  = useRef<HTMLDivElement>(null);

  // ── Live price refresh (every 5s) + flash indicators ─────────────────────
  type QuoteMap = Record<string, number | null>;
  const [livePrices,      setLivePrices]      = useState<QuoteMap>({});
  const [liveChangePcts,  setLiveChangePcts]  = useState<Record<string, number | null>>({});
  const [priceFlash,    setPriceFlash]    = useState<Record<string, 'up' | 'down'>>({});
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);
  const prevPricesRef  = useRef<Record<string, number>>({});
  const flashTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const tickersRef     = useRef<string[]>([]);

  // Keep tickersRef in sync and seed prevPrices on first load
  useEffect(() => {
    tickersRef.current = items.map(i => i.ticker);
    items.forEach(i => {
      if (i.price != null && prevPricesRef.current[i.ticker] == null)
        prevPricesRef.current[i.ticker] = i.price;
    });
  }, [items]);

  // 5-second price refresh interval
  useEffect(() => {
    const tick = async () => {
      const syms = tickersRef.current;
      if (!syms.length) return;
      try {
        const quotes = await getScreenerPrices(syms);
        const flashes: Record<string, 'up' | 'down'> = {};
        const updated: QuoteMap = {};
        const updatedPcts: Record<string, number | null> = {};
        Object.entries(quotes).forEach(([sym, q]) => {
          const newP = q.price ?? null;
          const oldP = prevPricesRef.current[sym];
          if (newP != null && oldP != null && newP !== oldP)
            flashes[sym] = newP > oldP ? 'up' : 'down';
          if (newP != null) prevPricesRef.current[sym] = newP;
          updated[sym] = newP;
          if (q.change_pct !== undefined) updatedPcts[sym] = q.change_pct ?? null;
        });
        setLivePrices(prev => ({ ...prev, ...updated }));
        if (Object.keys(updatedPcts).length) setLiveChangePcts(prev => ({ ...prev, ...updatedPcts }));
        setLastRefreshed(new Date());
        if (Object.keys(flashes).length) {
          setPriceFlash(prev => ({ ...prev, ...flashes }));
          Object.keys(flashes).forEach(sym => {
            if (flashTimersRef.current[sym]) clearTimeout(flashTimersRef.current[sym]);
            flashTimersRef.current[sym] = setTimeout(() =>
              setPriceFlash(prev => { const n = { ...prev }; delete n[sym]; return n; }), 1800);
          });
        }
      } catch { /* silent */ }
    };
    const id = setInterval(tick, 15000);
    return () => clearInterval(id);
  }, []);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await getWatchlist());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setShowSugg(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleInputChange = (raw: string) => {
    setAddInput(raw);
    setSelectedTicker(null);
    setAddError(null);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = raw.trim();
    if (q.length < 1) { setSuggestions([]); setShowSugg(false); return; }
    setSuggLoading(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const results = await searchCompanies(q, 8);
        setSuggestions(results);
        setShowSugg(results.length > 0);
        if (results.length === 0 && q.length >= 2) {
          setAddError('No matching ticker found.');
        }
      } catch {
        setSuggestions([]);
        setShowSugg(false);
      } finally {
        setSuggLoading(false);
      }
    }, 280);
  };

  const handleSelectSuggestion = (s: CompanySearchResult) => {
    setAddInput(`${s.ticker} – ${s.name}`);
    setSelectedTicker(s.ticker);
    setSuggestions([]);
    setShowSugg(false);
    setAddError(null);
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const ticker = selectedTicker ?? addInput.trim().toUpperCase().split(/[\s–-]/)[0];
    if (!ticker) return;

    // Tier limit check
    const { watchlistLimit } = getProfile();
    if (items.length >= watchlistLimit) {
      setAddError(`Your plan allows up to ${watchlistLimit} watchlist tickers. Upgrade to add more.`);
      return;
    }

    setAdding(true);
    setAddError(null);
    try {
      const item = await addToWatchlist(ticker);
      setItems(prev => {
        if (prev.some(i => i.ticker === item.ticker)) return prev;
        return [item, ...prev];
      });
      setAddInput('');
      setSelectedTicker(null);
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : String(e));
    } finally {
      setAdding(false);
    }
  };

  const handleRemove = async (ticker: string) => {
    setRemoving(ticker);
    try {
      await removeFromWatchlist(ticker);
      setItems(prev => prev.filter(i => i.ticker !== ticker));
    } catch {
      // leave item visible on failure
    } finally {
      setRemoving(null);
    }
  };

  // ── MOBILE LAYOUT ─────────────────────────────────────────────────────────────
  // Aligned with HistoryPage / ScreenerPage: neutral zinc background (white in light,
  // token-driven cream/forest surfaces, shared GradeChip for VGPM.
  if (mode === 'mobile') {
    return (
      <div className="min-h-full flex flex-col bg-background">
        <TabHero title="Watchlist" />

        <div className="px-4 py-3 space-y-3">
          {/* Add ticker */}
          <form onSubmit={handleAdd} className="flex items-center gap-2">
            <div ref={wrapperRef} className="relative flex-1">
              <input
                value={addInput}
                onChange={e => handleInputChange(e.target.value)}
                onFocus={() => { if (suggestions.length > 0) setShowSugg(true); }}
                placeholder="Search ticker or company…"
                className="w-full h-11 rounded-lg bg-muted/60 border border-border px-4 text-[13px] focus:bg-card focus:border-brand/40 focus:outline-none focus:ring-2 focus:ring-brand/10 placeholder:text-muted-foreground/70 text-foreground"
                disabled={adding}
                autoComplete="off"
              />
              {showSugg && suggestions.length > 0 && (
                <div className="absolute top-full left-0 mt-1 w-full bg-card border border-border rounded-lg shadow-lg z-50 overflow-hidden">
                  {suggestions.map(s => (
                    <button
                      key={s.ticker}
                      type="button"
                      className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-muted/60 transition-colors"
                      onMouseDown={e => { e.preventDefault(); handleSelectSuggestion(s); }}
                    >
                      <span className="font-mono font-semibold text-sm w-14 shrink-0 text-foreground">{s.ticker}</span>
                      <span className="text-sm text-muted-foreground truncate">{s.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <Button type="submit" disabled={adding || !addInput.trim()} className="h-11 rounded-lg px-4">
              {adding ? '...' : '+ Add'}
            </Button>
          </form>
          {addError && <p className="text-xs text-content-high">{addError}</p>}

          {/* Column headers — V/G/P/M letters once; row grades align beneath */}
          <div className="flex items-center px-3 mb-1">
            <div className="flex-1" />
            <div className="flex items-center gap-0">
              <span className="w-9 text-center text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70">V</span>
              <span className="w-9 text-center text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70">G</span>
              <span className="w-9 text-center text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70">P</span>
              <span className="w-9 text-center text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70">M</span>
            </div>
          </div>

          {/* Watchlist cards */}
          {loading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">Loading watchlist...</div>
          ) : items.length === 0 ? (
            <div className="py-8 text-center text-sm text-muted-foreground">Your watchlist is empty. Add a ticker above.</div>
          ) : (
            <div className="rounded-lg border border-border bg-card overflow-hidden shadow-sm">
              {items.map((item, i) => {
                const price = livePrices[item.ticker] ?? item.price;
                const pct = liveChangePcts[item.ticker] ?? item.change_pct;
                return (
                  <SwipeableCard
                    key={item.ticker}
                    onClick={() => navigate(`/report/${item.ticker}`)}
                    className={`w-full bg-card px-3 py-4 text-left flex items-center cursor-pointer ${i > 0 ? 'border-t border-border/60' : ''}`}
                    actions={[
                      {
                        icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>,
                        color: 'bg-blue-500',
                        onClick: () => { sessionStorage.setItem('watchlist_analyze', item.ticker); navigate('/report'); },
                      },
                      {
                        icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>,
                        color: 'bg-surface-2',
                        onClick: () => handleRemove(item.ticker),
                      },
                    ]}
                  >
                    {/* Left: Ticker + Company stacked — only this subtree opens
                        the report. Taps on price / VGPM do nothing; swipe
                        actions still work anywhere on the card. */}
                    <div data-tap="open" className="shrink-0 w-[80px] active:bg-muted/60 rounded-md -m-1 p-1 cursor-pointer">
                      <span className="font-bold text-[15px] leading-none block text-foreground">{item.ticker}</span>
                      <span className="text-[10px] text-muted-foreground leading-tight block mt-1">
                        {item.companyName}
                      </span>
                    </div>

                    {/* Center: Price + %Change, vertically centered */}
                    <div className="shrink-0 text-center">
                      <span className="text-sm font-semibold tabular-nums block leading-tight text-foreground">
                        {price != null ? `$${price.toFixed(2)}` : '—'}
                      </span>
                      {pct != null && (
                        <span className={`text-[10px] font-semibold tabular-nums block leading-tight ${pct >= 0 ? 'text-gain' : 'text-loss'}`}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
                        </span>
                      )}
                    </div>

                    {/* Right: V G P M — bare grades under the column header */}
                    <div className="flex items-center gap-0 ml-auto">
                      {VGPM_DIMS.map(dim => (
                        <div key={dim} className="w-9 flex items-center justify-center">
                          <GradeChip grade={item.vgpm?.[dim]?.grade} />
                        </div>
                      ))}
                    </div>
                  </SwipeableCard>
                );
              })}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── DESKTOP LAYOUT ──────────────────────────────────────────────────────────
  return (
    <>
    <TabHero
      title="Watchlist"
      actions={lastRefreshed
        ? <span className="text-xs text-current opacity-70 whitespace-nowrap">Prices updated {lastRefreshed.toLocaleTimeString()}</span>
        : undefined}
    />
    <PageContainer size="wide" flush className="py-5 md:py-6">

          {/* Add ticker */}
          <Card className="p-4 mb-6">
            <form onSubmit={handleAdd} className="flex items-start gap-3">
              <div ref={wrapperRef} className="relative">
                <div className="flex items-center gap-2">
                  <input
                    value={addInput}
                    onChange={e => handleInputChange(e.target.value)}
                    onFocus={() => { if (suggestions.length > 0) setShowSugg(true); }}
                    placeholder="Search ticker or company…"
                    className="h-9 w-72 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    disabled={adding}
                    autoComplete="off"
                  />
                  {suggLoading && (
                    <div className="absolute right-3 top-2.5 w-4 h-4 border-2 border-border border-t-primary rounded-full animate-spin" />
                  )}
                </div>

                {/* Suggestions dropdown */}
                {showSugg && suggestions.length > 0 && (
                  <div className="absolute top-full left-0 mt-1 w-72 bg-background border border-border rounded-md shadow-lg z-50 overflow-hidden">
                    {suggestions.map(s => (
                      <button
                        key={s.ticker}
                        type="button"
                        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-muted transition-colors"
                        onMouseDown={e => { e.preventDefault(); handleSelectSuggestion(s); }}
                      >
                        <span className="font-mono font-semibold text-sm w-16 shrink-0">{s.ticker}</span>
                        <span className="text-sm text-muted-foreground truncate">{s.name}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <Button type="submit" disabled={adding || !addInput.trim()} className="flex items-center gap-1.5">
                {adding ? 'Adding…' : (
                  <>
                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
                    </svg>
                    Add
                  </>
                )}
              </Button>
            </form>
            {addError && (
              <p className="mt-2 text-xs text-content-high">
                {addError}
                {addError.includes('Upgrade') && (
                  <a href="#/pricing" className="ml-1 underline underline-offset-2 font-semibold hover:opacity-80">
                    View plans →
                  </a>
                )}
              </p>
            )}
          </Card>

          {/* Error */}
          {error && (
            <div className="mb-4 p-3 border border-[var(--hairline)] rounded text-sm text-content-high">{error}</div>
          )}

          {/* Table */}
          {loading ? (
            <div className="text-sm text-muted-foreground animate-pulse">Loading watchlist…</div>
          ) : items.length === 0 ? (
            <Card className="p-8 text-center text-muted-foreground text-sm">
              Your watchlist is empty. Add a ticker above to get started.
            </Card>
          ) : (
            <div className="rounded border overflow-x-auto bg-card [&_table]:bg-card [&_tr]:bg-card [&_td]:bg-card [&_th]:bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Ticker</TableHead>
                    <TableHead>Company</TableHead>
                    <TableHead className="text-right">Price</TableHead>
                    <TableHead className="text-right">% Change</TableHead>
                    <TableHead className="text-center">Valuation</TableHead>
                    <TableHead className="text-center">Growth</TableHead>
                    <TableHead className="text-center">Profitability</TableHead>
                    <TableHead className="text-center">Momentum</TableHead>
                    <TableHead className="text-center">Score</TableHead>
                    <TableHead className="text-right text-muted-foreground text-xs">Added</TableHead>
                    <TableHead className="w-24" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map(item => (
                    <TableRow
                      key={item.ticker}
                      className="group hover:bg-muted/40 cursor-pointer"
                      onClick={() => navigate(`/report/${item.ticker}`)}
                    >
                      <TableCell className="font-mono font-bold text-sm">
                        {item.ticker}
                      </TableCell>
                      <TableCell className="text-sm max-w-[200px] truncate text-muted-foreground">
                        {item.companyName}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm">
                        {(() => {
                          const price = livePrices[item.ticker] ?? item.price;
                          const flash = priceFlash[item.ticker];
                          return (
                            <span className={`inline-flex items-center gap-0.5 transition-colors duration-700 ${
                              flash === 'up'   ? 'text-gain' :
                              flash === 'down' ? 'text-loss'   : ''
                            }`}>
                              {price != null ? `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
                              {flash === 'up'   && <span className="text-[10px] leading-none">▲</span>}
                              {flash === 'down' && <span className="text-[10px] leading-none">▼</span>}
                            </span>
                          );
                        })()}
                      </TableCell>
                      <TableCell className="text-right font-mono text-sm tabular-nums">
                        {(() => {
                          const pct = liveChangePcts[item.ticker] ?? item.change_pct;
                          if (pct == null) return <span className="text-muted-foreground/40">—</span>;
                          return (
                            <span className={pct >= 0 ? 'text-gain' : 'text-loss'}>
                              {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
                            </span>
                          );
                        })()}
                      </TableCell>
                      {VGPM_DIMS.map(dim => (
                        <TableCell key={dim} className="text-center">
                          <GradePill grade={item.vgpm?.[dim]?.grade} />
                        </TableCell>
                      ))}
                      <TableCell className="text-center">
                        {item.composite_score != null ? (
                          <span className="font-bold text-sm">{item.composite_score}</span>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right text-xs text-muted-foreground whitespace-nowrap">
                        {new Date(item.addedAt).toLocaleDateString()}
                      </TableCell>
                      <TableCell onClick={e => e.stopPropagation()}>
                        <div className="flex items-center gap-1 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            type="button"
                            title="Agentic analysis"
                            onClick={() => {
                              sessionStorage.setItem('watchlist_analyze', item.ticker);
                              navigate('/report');
                            }}
                            className="p-1 rounded text-muted-foreground/60 hover:text-foreground hover:bg-muted transition-colors"
                          >
                            <AgentOrbIcon size={18} />
                          </button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-xs h-7 px-2 text-content-high hover:text-content-high"
                            disabled={removing === item.ticker}
                            onClick={() => handleRemove(item.ticker)}
                          >
                            {removing === item.ticker ? '…' : '✕'}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
    </PageContainer>
    </>
  );
}

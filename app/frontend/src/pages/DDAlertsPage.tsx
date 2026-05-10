/**
 * DDAlertsPage.tsx — Auto Due-D dashboard.
 *
 * Three sections:
 *   1. Today (live, auto-refresh every 5 min)
 *   2. Sector clusters (when active)
 *   3. EOD digest — top movers each direction
 *
 * Filters: direction (DROP / PUMP / All), tier (held / active / news / all).
 * Cards use the same 4-palette colors as Slack so cross-references are obvious.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { listDdAlerts, getDdDigestToday } from '@/lib/api';
import type { DdAlert, DdDigest, DdDirection } from '@/lib/reportTypes';
import { AlertCard } from '@/components/dd/AlertCard';
import { DigestPanel } from '@/components/dd/DigestPanel';

const REFRESH_MS = 5 * 60 * 1000;   // 5 min auto-refresh

type DirFilter  = 'all' | DdDirection;
type TierFilter = 'all' | 'tier1_held' | 'tier2_active' | 'news_trigger' | 'admin_trigger';

const TIER_LABELS: Record<TierFilter, string> = {
  all:             'All tiers',
  tier1_held:      'Held',
  tier2_active:    'Active',
  news_trigger:    'News',
  admin_trigger:   'Admin',
};


export function DDAlertsPage() {
  const [alerts,  setAlerts]  = useState<DdAlert[] | null>(null);
  const [digest,  setDigest]  = useState<DdDigest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  const [direction, setDirection] = useState<DirFilter>('all');
  const [tier,      setTier]      = useState<TierFilter>('all');

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [a, d] = await Promise.all([
        listDdAlerts({ limit: 100 }),
        getDdDigestToday(),
      ]);
      setAlerts(a);
      setDigest(d);
      setLastRefreshed(new Date());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch + 5-min poll
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(id);
  }, [refresh]);

  // Apply client-side filters
  const filtered = useMemo(() => {
    if (!alerts) return [];
    return alerts.filter(a => {
      if (direction !== 'all' && a.last_direction !== direction) return false;
      if (tier      !== 'all' && a.tier           !== tier)      return false;
      return true;
    });
  }, [alerts, direction, tier]);

  // "Today" cutoff = UTC start of day (matches backend digest semantics)
  const todayIso = new Date().toISOString().slice(0, 10);
  const todayAlerts = useMemo(() => {
    return filtered.filter(a => a.last_triggered_at >= todayIso);
  }, [filtered, todayIso]);

  return (
    <div className="px-4 py-4 max-w-3xl mx-auto space-y-4">
      {/* Header */}
      <header className="flex items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-bold text-foreground">Auto Due-D</h1>
          <p className="text-[11px] text-muted-foreground">
            Bidirectional ±10% movement detection · directional cooldown ·
            real-time Slack push + persistent dashboard
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="flex items-center gap-1.5 text-[10px] text-muted-foreground hover:text-foreground px-2 py-1 rounded border border-border bg-muted/30"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
          {lastRefreshed
            ? `${lastRefreshed.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`
            : 'Refresh'}
        </button>
      </header>

      {/* Error banner */}
      {error && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-700 dark:text-red-400">
          Failed to load alerts: {error}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Direction</span>
        <FilterChips
          value={direction}
          onChange={setDirection}
          options={[
            { value: 'all',  label: 'All' },
            { value: 'DROP', label: 'Drops' },
            { value: 'PUMP', label: 'Pumps' },
          ]}
        />
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground ml-2">Tier</span>
        <FilterChips
          value={tier}
          onChange={setTier}
          options={(Object.keys(TIER_LABELS) as TierFilter[]).map(v => ({
            value: v, label: TIER_LABELS[v],
          }))}
        />
      </div>

      {/* Section 1 — Today (live) */}
      <section>
        <h2 className="text-[11px] uppercase tracking-widest text-muted-foreground mb-2">
          Today (auto-refresh 5m)
        </h2>
        {loading && !alerts ? (
          <div className="text-sm text-muted-foreground italic px-2 py-3">Loading…</div>
        ) : todayAlerts.length === 0 ? (
          <div className="text-sm text-muted-foreground italic px-2 py-3">
            No alerts today matching current filters.
          </div>
        ) : (
          <div className="space-y-2">
            {todayAlerts.map(a => (
              <AlertCard key={`${a.dd_run_id || a.ticker}-${a.last_triggered_at}`} alert={a} />
            ))}
          </div>
        )}
      </section>

      {/* Section 2 — EOD Digest (today's aggregate) */}
      <section>
        <h2 className="text-[11px] uppercase tracking-widest text-muted-foreground mb-2">
          EOD Digest — Today's Top Movers
        </h2>
        <DigestPanel digest={digest} loading={loading} />
      </section>

      {/* The Auto Due-D feed is intentionally daily-refreshing — yesterday's
          alerts disappear from this view by design. The dd_alerts table is
          retained server-side for ~7 days (DD_RETENTION_DAYS env, see
          src/agents/dd/alert_dedup.py::cleanup_old_alerts) so the audit trail
          isn't lost. Permanent ticker-research history lives in the History tab. */}
    </div>
  );
}


// ── Filter chip group ────────────────────────────────────────────────────────

interface FilterChipsProps<T extends string> {
  value:    T;
  onChange: (v: T) => void;
  options:  ReadonlyArray<{ value: T; label: string }>;
}

function FilterChips<T extends string>({ value, onChange, options }: FilterChipsProps<T>) {
  return (
    <div className="inline-flex rounded-md overflow-hidden border border-border">
      {options.map(opt => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`px-2.5 py-1 text-[11px] font-medium transition-colors
            ${value === opt.value
              ? 'bg-primary text-primary-foreground'
              : 'bg-muted text-muted-foreground hover:bg-muted/70'}`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// Default export for lazy-import compatibility (some bundlers prefer it)
export default DDAlertsPage;

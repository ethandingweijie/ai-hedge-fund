/**
 * PulseCard.tsx — M2 Track C2: Gemini-Flash-style instant recall.
 *
 * Fires GET /analysis/pulse?ticker=X when the user selects/types a ticker
 * on the landing page (debounced, before any full run). Two beats stream
 * in over SSE:
 *   beat 1 (pulse_prior, <0.5 s)  — the last report's decision/PT/thesis
 *                                   from pooled run memory (DB only);
 *   beat 2 (pulse_delta, ~10-25 s) — one freshness search since that
 *                                   report, or a discovery brief for
 *                                   tickers with no prior coverage.
 * Same-day repeats are served from the backend pulse cache (from_cache).
 * Nothing here ever starts a pipeline run — "Run full analysis" is the
 * explicit opt-in back to the deep path.
 */
import { useEffect, useRef, useState } from 'react';
import { pulseTicker } from '@/lib/api';
import { currencySymbol } from '@/lib/utils';
import { actionTone } from '@/lib/semanticColors';
import { Markdown } from '@/components/report/shared/Markdown';

interface PulsePrior {
  ticker: string;
  covered: boolean;
  run_id?: string;
  run_at?: string;
  age_days?: number;
  price_at_run?: number;
  final_action?: string;
  price_target?: number;
  recap_text?: string;
  catalysts?: string[];
  summary?: string;
}

interface PulseDeltaEvent { headline?: string; date?: string; relevance?: string; }

interface PulseDelta {
  ticker: string;
  from_cache?: boolean;
  material?: boolean | null;
  events?: PulseDeltaEvent[];
  verdict?: string;
  discovery?: boolean;
  brief?: string;
}


/* Age badge — same fresh/stale convention as the History page (M1-8). */
function AgeBadge({ ageDays }: { ageDays?: number }) {
  if (ageDays == null) return null;
  const fresh = ageDays < 7;
  const label = ageDays <= 0 ? 'today' : `${Math.floor(ageDays)}d ago`;
  return (
    <span
      title={fresh ? 'Report is fresh (under 7 days)' : 'Report is 7+ days old — consider re-running'}
      className={`text-[9px] font-semibold tabular-nums px-1.5 py-0.5 rounded-full border whitespace-nowrap ${
        fresh
          ? 'bg-surface-2 text-content-high border-[var(--hairline)]'
          : 'bg-amber-500/10 text-amber-500 border-amber-500/20'
      }`}
    >
      {label}
    </span>
  );
}

interface Props {
  /** Current value of the landing search input. */
  ticker: string;
  onOpenReport: (runId: string) => void;
  onRunFull: () => void;
  /** Hide the card while the autocomplete dropdown is open (the fetch
   *  keeps running — the card pops in the moment the dropdown closes). */
  suppressed?: boolean;
}

export function PulseCard({ ticker, onOpenReport, onRunFull, suppressed }: Props) {
  const [prior, setPrior]   = useState<PulsePrior | null>(null);
  const [delta, setDelta]   = useState<PulseDelta | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError]   = useState<string | null>(null);
  const reqSeqRef = useRef(0);

  const t = ticker.trim().toUpperCase();
  const valid = /^[A-Z0-9][A-Z0-9.\-]{0,15}$/.test(t);

  useEffect(() => {
    // Reset immediately on ticker change — never show another ticker's pulse.
    setPrior(null); setDelta(null); setError(null); setSearching(false);
    if (!valid) return;

    const seq = ++reqSeqRef.current;
    let cancelled = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const runPulse = async (isRetry: boolean) => {
      if (cancelled || seq !== reqSeqRef.current) return;
      setSearching(true);
      controller = new AbortController();
      let settled = false; // a pulse_delta or pulse_error arrived on this stream
      try {
        const res = await pulseTicker(t, controller.signal);
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx: number;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            let name = '';
            let data = '';
            for (const line of frame.split('\n')) {
              if (line.startsWith('event: ')) name = line.slice(7);
              else if (line.startsWith('data: ')) data = line.slice(6);
            }
            if (!name || !data) continue; // keep-alive comments, heartbeats
            if (cancelled || seq !== reqSeqRef.current) return;
            try {
              const payload = JSON.parse(data);
              if (name === 'pulse_prior') { setPrior(payload); setSearching(true); }
              else if (name === 'pulse_delta') { settled = true; setDelta(payload); setSearching(false); }
              else if (name === 'pulse_error') { settled = true; setError(payload.error ?? 'pulse failed'); setSearching(false); }
              else if (name === 'pulse_complete') { setSearching(false); }
            } catch { /* malformed frame — skip */ }
          }
        }
      } catch (exc) {
        if ((exc as Error)?.name === 'AbortError') return;
        if (!cancelled && seq === reqSeqRef.current) {
          settled = true;
          setError('Pulse unavailable');
          setSearching(false);
        }
      }
      if (cancelled || seq !== reqSeqRef.current || settled) return;
      // Stream closed with no delta/error (edge proxy cut, or the server is
      // still searching after dropping the client — the beat-2 task caches
      // its result server-side regardless). One silent retry: by then the
      // delta is usually cached and arrives instantly. After the retry,
      // stop the spinner instead of turning forever.
      if (!isRetry) {
        retryTimer = setTimeout(() => { runPulse(true); }, 12_000);
      } else {
        setSearching(false);
      }
    };

    // Debounce: wait for typing to settle before firing the pulse
    // (300 ms — aligned with the 280 ms company-search debounce).
    const timer = setTimeout(() => { runPulse(false); }, 300);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      if (retryTimer) clearTimeout(retryTimer);
      try { controller?.abort(); } catch { /* ignore */ }
    };
  }, [t, valid]);

  const showSkeleton = searching && !prior && !delta && !error;
  if (!valid || (!prior && !delta && !error && !showSkeleton)) return null;

  const sym = currencySymbol(t);

  return (
    <div className={`mx-4 mt-3 rounded-xl border border-border bg-card/95 backdrop-blur shadow-sm p-4 max-h-[38vh] overflow-y-auto${suppressed ? ' hidden' : ''}`}>
      {/* Header row */}
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">
          Pulse · past research
        </span>
        <span className="text-[11px] font-semibold text-foreground tabular-nums">{t}</span>
        {prior?.covered && <AgeBadge ageDays={prior.age_days} />}
        {delta?.from_cache && (
          <span className="text-[9px] font-medium px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
            cached today
          </span>
        )}
        <span className="ml-auto">
          {searching && (
            <span className="inline-flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <span className="w-2.5 h-2.5 rounded-full border border-brand/40 border-t-brand animate-spin" />
              checking latest…
            </span>
          )}
        </span>
      </div>

      {showSkeleton ? (
        /* Fetch fired, beat 1 not back yet — show something immediately
           instead of nothing (the old null-render hid the card for ~1 s+). */
        <p className="text-[11px] text-muted-foreground">
          Recalling past research…
        </p>
      ) : (
      <>
      {/* Beat 1 — accumulated knowledge */}
      {prior?.covered ? (
        <div className="text-[12px] leading-relaxed">
          <div className="flex items-center gap-2 flex-wrap">
            {prior.final_action && (
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${actionTone(prior.final_action)}`}>
                {prior.final_action}
              </span>
            )}
            {prior.price_target != null && (
              <span className="text-foreground font-medium tabular-nums">
                PT {sym}{prior.price_target.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </span>
            )}
            {prior.price_at_run != null && (
              <span className="text-muted-foreground tabular-nums">
                @ {sym}{prior.price_at_run.toLocaleString(undefined, { maximumFractionDigits: 2 })} then
              </span>
            )}
          </div>
          {prior.recap_text && (
            <p className="text-muted-foreground mt-1">{prior.recap_text}</p>
          )}
          {(prior.catalysts?.length ?? 0) > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap mt-1.5">
              {(prior.catalysts ?? []).slice(0, 4).map((c, i) => (
                <span key={i} className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted/70 text-muted-foreground">
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>
      ) : prior ? (
        <Markdown className="[&_p]:text-[12px] [&_p]:text-muted-foreground [&_li]:text-[12px] [&_p]:mb-2">
          {prior.summary}
        </Markdown>
      ) : null}

      {/* Beat 2 — what changed since */}
      {delta && (
        <div className="mt-2.5 pt-2.5 border-t border-border/60">
          {delta.discovery && delta.brief ? (
            /* The discovery brief is LLM-written markdown — headings, bold
               labels, bullet lists. Rendering it as preformatted text leaked
               the markup verbatim ("**Sembcorp Industries Ltd ...**"). The
               arbitrary variants keep Pulse's compact 12px scale, since the
               shared Markdown map is sized for full report body copy. */
            <Markdown className="[&_p]:text-[12px] [&_li]:text-[12px] [&_p]:mb-2 [&_h3]:text-[13px] [&_h4]:text-[12px] [&_h5]:text-[12px] [&_h6]:text-[11px]">
              {delta.brief}
            </Markdown>
          ) : (delta.events?.length ?? 0) > 0 ? (
            <ul className="space-y-1">
              {(delta.events ?? []).map((ev, i) => (
                <li key={i} className="text-[12px] flex gap-2">
                  {ev.date && <span className="text-muted-foreground/70 tabular-nums shrink-0">{ev.date}</span>}
                  <span className="text-foreground/85 min-w-0">
                    {ev.headline}
                    {ev.relevance && <span className="text-muted-foreground"> — {ev.relevance}</span>}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          {delta.verdict && (
            <p className={`text-[11px] mt-1.5 font-medium ${
              delta.material === true
                ? 'text-amber-600 dark:text-amber-400'
                : delta.material === false
                  ? 'text-content-high'
                  : 'text-muted-foreground'
            }`}
            >
              {delta.verdict}
            </p>
          )}
        </div>
      )}

      {error && !prior && !delta && (
        <p className="text-[11px] text-muted-foreground">{error}</p>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 mt-3">
        {prior?.covered && prior.run_id && (
          <button
            type="button"
            onClick={() => onOpenReport(prior.run_id!)}
            className="h-8 px-3 text-[11px] font-semibold rounded-full border border-border bg-card text-foreground hover:bg-muted/60 transition-colors"
          >
            Open full report
          </button>
        )}
        <button
          type="button"
          onClick={onRunFull}
          className="h-8 px-3 text-[11px] font-semibold rounded-full bg-primary text-primary-foreground hover:bg-primary/85 transition-colors"
        >
          Run full analysis
        </button>
      </div>
      </>
      )}
    </div>
  );
}

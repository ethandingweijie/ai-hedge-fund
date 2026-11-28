/**
 * AskCard.tsx — answer to a question about a recent market development (owner, 2026-11-28).
 *
 * The landing box takes a question as well as a ticker. POST /analysis/ask runs one web search, answers with
 * sources, and lists the companies the development affects with their latest shared analysis. A VERIFIED
 * development (dated, two or more independent sources) can update an affected company's analysis: the run folds
 * it in as material news and the new report supersedes the shared one. An unverified one says why and offers
 * nothing to update.
 */
import { useEffect, useState } from 'react';
import { askMarket, type AskResult, type AskTicker } from '@/lib/api';
import { currencySymbol } from '@/lib/utils';
import { actionTone } from '@/lib/semanticColors';

interface Props {
  question: string;
  /** Bumped on every submit so asking the same question again re-runs it. */
  nonce: number;
  onOpenReport: (runId: string) => void;
  onUpdate: (ticker: string, askId: string) => void;
  onClose: () => void;
}

function plural(n: number, w: string) { return `${n} ${w}${n === 1 ? '' : 's'}`; }

function TickerRow({ row, askId, onOpenReport, onUpdate }: {
  row: AskTicker; askId: string; onOpenReport: (id: string) => void; onUpdate: (t: string, a: string) => void;
}) {
  const sym = currencySymbol(row.ticker);
  const latestDate = row.latest?.run_at ? row.latest.run_at.slice(0, 10) : null;
  return (
    <div className="py-2.5 border-t border-border/60 first:border-t-0">
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] font-semibold tabular-nums text-foreground">{row.ticker}</span>
        <span className="text-xs text-muted-foreground truncate">{row.name}</span>
      </div>
      {row.why && <p className="text-xs text-muted-foreground mt-0.5">{row.why}</p>}
      <div className="flex flex-wrap items-center gap-2 mt-1.5">
        {row.latest ? (
          <button type="button" onClick={() => onOpenReport(row.latest!.run_id)}
                  className="text-xs text-foreground underline underline-offset-2 hover:text-muted-foreground">
            Latest analysis {latestDate}
            {row.latest.final_action && (
              <span className={`ml-1 font-semibold ${actionTone(row.latest.final_action, 'text')}`}>{row.latest.final_action}</span>
            )}
            {typeof row.latest.price_target === 'number' && (
              <span className="ml-1 tabular-nums">· PT {sym}{row.latest.price_target.toFixed(2)}</span>
            )}
          </button>
        ) : (
          <span className="text-xs text-muted-foreground">Not analysed yet</span>
        )}
        {row.can_update && (
          <button type="button" onClick={() => onUpdate(row.ticker, askId)}
                  className="ml-auto h-7 px-3 rounded-full bg-primary text-primary-foreground text-xs font-semibold">
            {row.latest ? 'Update analysis' : 'Run analysis'} with this · {plural(row.credits, 'credit')}
          </button>
        )}
      </div>
    </div>
  );
}

export function AskCard({ question, nonce, onOpenReport, onUpdate, onClose }: Props) {
  const [res, setRes] = useState<AskResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    const ctrl = new AbortController();
    setBusy(true); setErr(null); setRes(null);
    askMarket(question, undefined, ctrl.signal)
      .then(r => setRes(r))
      .catch(e => { if (!ctrl.signal.aborted) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!ctrl.signal.aborted) setBusy(false); });
    return () => ctrl.abort();
  }, [question, nonce]);

  return (
    <div className="mx-4 mt-3 rounded-xl border border-border bg-card/95 backdrop-blur shadow-sm p-4 max-h-[60vh] overflow-y-auto">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground/70">Market question</span>
        <button type="button" onClick={onClose} aria-label="Close" className="ml-auto text-xs text-muted-foreground hover:text-foreground">
          Close
        </button>
      </div>
      <p className="text-[13px] font-medium text-foreground mb-2">{question}</p>

      {busy && (
        <div className="space-y-2" aria-busy="true">
          <p className="text-xs text-muted-foreground animate-pulse">Searching recent reporting…</p>
          <div className="h-3 rounded bg-muted/70 w-11/12" />
          <div className="h-3 rounded bg-muted/70 w-9/12" />
        </div>
      )}
      {err && <p className="text-xs text-foreground">{err}</p>}

      {res && (
        <>
          <p className="text-[13px] leading-relaxed text-foreground whitespace-pre-line">{res.answer}</p>

          {res.development && (
            <div className="mt-3 rounded-lg bg-surface-2 px-3 py-2">
              <p className="text-xs font-semibold text-foreground">
                {res.development.headline}
                {res.development.date && <span className="font-normal text-muted-foreground"> · {res.development.date}</span>}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {res.verified ? `Verified. ${res.verification}` : `Not verified. ${res.verification}`}
              </p>
            </div>
          )}
          {!res.development && <p className="text-xs text-muted-foreground mt-2">{res.verification}</p>}

          {res.sources.length > 0 && (
            <ol className="mt-3 space-y-1">
              {res.sources.map((s, i) => (
                <li key={i} className="text-xs text-muted-foreground truncate">
                  <span className="tabular-nums">{i + 1}.</span>{' '}
                  <a href={s.url} target="_blank" rel="noopener noreferrer nofollow"
                     className="underline underline-offset-2 hover:text-foreground">{s.title || s.url}</a>
                  {s.date && <span> · {s.date}</span>}
                </li>
              ))}
            </ol>
          )}

          {res.tickers.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground/70 mb-1">Affected companies</p>
              {res.tickers.map(r => (
                <TickerRow key={r.ticker} row={r} askId={res.ask_id} onOpenReport={onOpenReport} onUpdate={onUpdate} />
              ))}
              {res.tickers.some(r => r.can_update) && (
                <p className="text-xs text-muted-foreground mt-2">
                  An update folds this development into the analysis, and the new report replaces the latest one for everyone.
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

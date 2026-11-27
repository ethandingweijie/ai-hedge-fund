/**
 * Ask AI about this report (owner, 2026-11-27).
 *
 * The AI agent used to be reachable only from the Valuation tab, under ~20
 * engine inputs, while the prominent "Discuss" button opened a community forum
 * where a novice's question got no answer. This puts the agent at the top of
 * every report, with starter questions, on both render paths:
 *
 *   - desktop: AskAiEntry in the ReportHero action rail -> right-hand sheet
 *   - mobile:  AskAiButton in the V2ReportView header -> bottom sheet
 *
 * Same endpoint as the estimate workbench (POST /analysis/runs/{id}/estimates/ask,
 * estimate_agent_service.ask); the backend prompt answers in plain words first.
 * A what-if proposal is shown as text only here: changing model inputs stays in
 * Valuation > Advanced (model inputs), where the user recomputes and saves.
 */
import { useState, type ReactNode } from 'react';
import { Sparkles, Send } from 'lucide-react';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { askEstimateAgent } from '@/lib/api';
import type { EstimateAskResponse } from '@/lib/reportTypes';
import { useI18n } from '@/i18n';

/** Visible chip text -> the question sent (the longer form gives the agent the intent). */
export const STARTER_QUESTIONS: { label: string; question: string }[] = [
  { label: 'Should I buy?', question: 'Should I buy this stock? Explain what the rating means for someone deciding today, in plain words.' },
  { label: 'Why this rating?', question: 'Why does this report give this rating, and what changed since the previous report?' },
  { label: 'What are the risks?', question: 'What are the main risks to this view, in plain words?' },
  { label: 'Explain the terms', question: 'Explain the key terms used in this report (target, intrinsic value, total shareholder return, benchmark) in plain words.' },
];

type Msg = { role: 'user' | 'agent'; content: string; sources?: EstimateAskResponse['sources']; proposal?: boolean };

function useAskAi(runId: string | null | undefined, ticker: string) {
  const { t } = useI18n();
  const [thread, setThread] = useState<Msg[]>([]);
  const [busy, setBusy] = useState(false);
  async function ask(q: string, shown?: string) {
    const question = q.trim();
    if (!question || busy || !runId) return;
    const history = thread.map((m) => ({ role: m.role, content: m.content }));
    setThread((th) => [...th, { role: 'user', content: shown ?? question }]);
    setBusy(true);
    try {
      const r = await askEstimateAgent(runId, ticker, question, history);
      setThread((th) => [...th, { role: 'agent', content: r.answer, sources: r.sources, proposal: !!r.proposal }]);
    } catch (e) {
      const msg = e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : String(e);
      setThread((th) => [...th, { role: 'agent', content: t('The AI did not answer: {msg}', { msg }) }]);
    } finally {
      setBusy(false);
    }
  }
  return { thread, busy, ask };
}

function Conversation({ runId, ticker, state }: { runId: string | null | undefined; ticker: string; state: ReturnType<typeof useAskAi> }) {
  const { t } = useI18n();
  const [q, setQ] = useState('');
  const { thread, busy, ask } = state;
  return (
    <div className="flex flex-col gap-4 min-h-0 flex-1">
      <div className="flex flex-wrap gap-2">
        {STARTER_QUESTIONS.map((s) => (
          <button
            key={s.label}
            type="button"
            disabled={busy || !runId}
            onClick={() => void ask(s.question, t(s.label))}
            className="h-8 px-3 rounded-full border border-border text-[13px] font-medium text-foreground hover:bg-muted disabled:opacity-50"
          >
            {t(s.label)}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto space-y-3 pr-1">
        {thread.length === 0 && (
          <p className="text-[14px] text-muted-foreground leading-relaxed">
            {runId
              ? t('Ask anything about this report. The AI answers from this report only and explains in plain words.')
              : t('The AI can answer once the report has finished.')}
          </p>
        )}
        {thread.map((m, i) => (
          <div key={i} className={m.role === 'user'
            ? 'ml-8 rounded-2xl bg-muted px-3.5 py-2.5 text-[14.5px] text-foreground'
            : 'mr-4 rounded-2xl border border-border px-3.5 py-2.5 text-[14.5px] leading-relaxed text-foreground/90 whitespace-pre-line'}>
            {m.content}
            {m.sources && m.sources.length > 0 && (
              <div className="mt-2 pt-2 border-t border-border/60 text-[12.5px] text-muted-foreground">
                <span className="font-medium">{t('Sources')}: </span>
                {m.sources.map((s, j) => (
                  <span key={j}>
                    {j > 0 ? '; ' : ''}
                    {s.url
                      ? <a href={s.url} target="_blank" rel="noopener noreferrer" className="tap-compact underline underline-offset-2 hover:text-foreground">{s.name}</a>
                      : s.name}
                    {s.date ? ` (${s.date})` : ''}
                  </span>
                ))}
              </div>
            )}
            {m.proposal && (
              <p className="mt-2 text-[12.5px] text-muted-foreground">
                {t('To try this change on the numbers, open Valuation > Advanced (model inputs).')}
              </p>
            )}
          </div>
        ))}
        {busy && <p className="text-[13px] text-muted-foreground">{t('Thinking…')}</p>}
      </div>
      <form
        className="flex gap-2 shrink-0"
        onSubmit={(e) => { e.preventDefault(); const v = q; setQ(''); void ask(v); }}
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          disabled={busy || !runId}
          placeholder={t('Ask about {ticker}…', { ticker })}
          className="h-10 flex-1 min-w-0 rounded-full border border-input bg-background px-4 text-[16px] focus:outline-none focus:ring-1 focus:ring-ring"
        />
        <button
          type="submit"
          disabled={busy || !runId || !q.trim()}
          aria-label={t('Send')}
          className="h-10 w-10 shrink-0 inline-flex items-center justify-center rounded-full bg-foreground text-background disabled:opacity-40"
        >
          <Send size={16} />
        </button>
      </form>
      <p className="text-[12px] text-muted-foreground shrink-0">
        {t('AI answers can be wrong and are not personal financial advice.')}
      </p>
    </div>
  );
}

function AskSheet({ open, onOpenChange, side, runId, ticker, state }: {
  open: boolean; onOpenChange: (o: boolean) => void; side: 'right' | 'bottom';
  runId: string | null | undefined; ticker: string; state: ReturnType<typeof useAskAi>;
}) {
  const { t } = useI18n();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side={side}
        className={side === 'bottom'
          ? 'h-[85vh] rounded-t-2xl flex flex-col gap-4 px-4 pb-[calc(env(safe-area-inset-bottom,0px)+16px)]'
          : 'w-full sm:max-w-md flex flex-col gap-4'}
      >
        <SheetHeader className="text-left">
          <SheetTitle>{t('Ask AI about this report')}</SheetTitle>
          <SheetDescription>{ticker}</SheetDescription>
        </SheetHeader>
        <Conversation runId={runId} ticker={ticker} state={state} />
      </SheetContent>
    </Sheet>
  );
}

/** Desktop: the action-rail entry, starter questions open the sheet and ask. */
export function AskAiEntry({ runId, ticker }: { runId: string | null | undefined; ticker: string }) {
  const { t } = useI18n();
  const state = useAskAi(runId, ticker);
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2.5">
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center justify-center gap-2 h-12 rounded-full bg-brand text-brand-foreground text-[15px] font-semibold hover:bg-brand/90 transition-colors"
      >
        <Sparkles size={16} />
        {t('Ask AI about this report')}
      </button>
      <div className="flex flex-wrap gap-1.5">
        {STARTER_QUESTIONS.map((s) => (
          <button
            key={s.label}
            type="button"
            disabled={!runId}
            onClick={() => { setOpen(true); void state.ask(s.question, t(s.label)); }}
            className="h-7 px-2.5 rounded-full border border-border text-[12.5px] text-foreground hover:bg-muted disabled:opacity-50"
          >
            {t(s.label)}
          </button>
        ))}
      </div>
      <AskSheet open={open} onOpenChange={setOpen} side="right" runId={runId} ticker={ticker} state={state} />
    </div>
  );
}

/** Mobile: a header pill that opens a bottom sheet. */
export function AskAiButton({ runId, ticker, children }: { runId: string | null | undefined; ticker: string; children?: ReactNode }) {
  const { t } = useI18n();
  const state = useAskAi(runId, ticker);
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 h-9 px-3.5 rounded-full bg-brand text-brand-foreground text-[13px] font-semibold active:bg-brand/90 shrink-0"
      >
        <Sparkles size={14} />
        {children ?? t('Ask AI')}
      </button>
      <AskSheet open={open} onOpenChange={setOpen} side="bottom" runId={runId} ticker={ticker} state={state} />
    </>
  );
}

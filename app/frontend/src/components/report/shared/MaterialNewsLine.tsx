/**
 * MaterialNewsLine — the plain-language "what's new since the last report"
 * line under the rating (owner, 2026-11-27).
 *
 *   What's new since 26 Nov 2026: 3 news items (show) · Effect on our numbers: …
 *
 * Built from research_view.material_update (the backend's computed sentence,
 * reader_text.material_update_sentence) and the freshness-check events. The
 * event list is collapsed by default. The backend sentence is kept verbatim
 * on hover for the audit trail. Shared by both render paths.
 */
import { useState } from 'react';
import type { FreshnessDelta, FreshnessDeltaEvent, ResearchView } from '@/lib/reportTypes';
import { useI18n } from '@/i18n';

const METRIC: Record<string, string> = {
  'revenue growth': 'Sales growth',
  'EBITDA margin': 'Operating margin (EBITDA)',
  'EPS': 'Profit per share (EPS)',
};
const YEAR: Record<string, string> = { 'FY+1': 'next fiscal year', 'FY+2': 'the year after' };

type Tr = (s: string, v?: Record<string, string | number>) => string;

/** "FY+2 revenue growth newly estimated at 3.5%" -> "Sales growth, the year after: first estimate 3.5%". */
function plainChange(bit: string, t: Tr): string {
  const m = bit.trim().match(/^(FY\+\d)\s+(.+?)\s+(newly estimated at|no longer estimated \(was|(\S+)\s+to)\s*(.*?)\)?$/);
  if (!m) return bit.trim();
  const [, fy, metricRaw, verb, from, rest] = m;
  const metric = t(METRIC[metricRaw] ?? metricRaw);
  const year = t(YEAR[fy] ?? fy);
  if (verb.startsWith('newly')) return t('{metric}, {year}: first estimate {v}', { metric, year, v: rest });
  if (verb.startsWith('no longer')) return t('{metric}, {year}: no longer estimated (was {v})', { metric, year, v: rest });
  return t('{metric}, {year}: {a} → {b}', { metric, year, a: from ?? '', b: rest });
}

function parseSentence(sentence: string): { events: FreshnessDeltaEvent[]; changes: string[] | null } {
  const ev = sentence.match(/previous report \((.+?)\)(?: is not yet|\.)/);
  const events = ev
    ? ev[1].split(/;\s+/).map((s) => {
        const d = s.match(/^(\d{4}-\d{2}-\d{2}):\s*(.+)$/);
        return d ? { date: d[1], headline: d[2] } : { headline: s };
      })
    : [];
  const ch = sentence.match(/Base-case estimates changed:\s*(.+?)\.?$/);
  if (ch) return { events, changes: ch[1].split(/,\s+(?=FY\+)/) };
  if (/did not change/.test(sentence)) return { events, changes: [] };
  return { events, changes: null };
}

function fmtDate(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function MaterialNewsLine({ view, delta, priorRunAt, compact = false }: {
  view: ResearchView;
  delta?: FreshnessDelta | null;
  priorRunAt?: string | null;
  compact?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const mu = view.material_update;
  if (!mu?.sentence) return null;

  const parsed = parseSentence(mu.sentence);
  const events = (delta?.events?.length ? delta.events : parsed.events).filter((e) => e.headline);
  const since = fmtDate(priorRunAt ?? delta?.prior_run_at);
  const size = compact ? 'text-[13px]' : 'text-sm';

  let effect: string;
  if (mu.status === 'not_reflected') effect = t('not in our numbers yet, so the rating is under review');
  else if (parsed.changes == null) effect = t('see the valuation tab');
  else if (parsed.changes.length === 0) effect = t('none, our estimates did not change');
  else effect = parsed.changes.map((c) => plainChange(c, t)).join('; ');

  return (
    <div className={`${size} leading-relaxed text-foreground/85`} title={mu.sentence}>
      <span className="font-semibold text-foreground">
        {since ? t("What's new since {date}", { date: since }) : t("What's new since our last report")}:
      </span>{' '}
      {events.length > 0 ? (
        <>
          {t(events.length === 1 ? '{n} news item' : '{n} news items', { n: events.length })}{' '}
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="tap-compact underline underline-offset-2 text-foreground hover:text-foreground/70"
          >
            {open ? t('hide') : t('show')}
          </button>
        </>
      ) : (
        t('news since our last report')
      )}
      <span className="text-muted-foreground"> · </span>
      <span className="font-medium text-foreground">{t('Effect on our numbers')}:</span> {effect}
      {open && events.length > 0 && (
        <ul className="mt-2 space-y-1.5 border-l border-border pl-3">
          {events.map((e, i) => (
            <li key={i} className={compact ? 'text-[12.5px]' : 'text-[13px]'}>
              <span className="text-foreground">{e.headline}</span>
              {e.date && <span className="text-muted-foreground"> ({e.date})</span>}
              {e.relevance && <p className="text-muted-foreground">{e.relevance}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

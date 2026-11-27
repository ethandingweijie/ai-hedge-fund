/**
 * Model Accuracy building blocks: chips, collapsible sections, a table that
 * becomes stacked key/value cards on a phone, and a pager. Monochrome
 * throughout: green and red are reserved for price change (lib/semanticColors.ts).
 */
import { useEffect, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { useI18n } from '@/i18n';

export function Chip({ children, strong = false }: { children: ReactNode; strong?: boolean }) {
  return (
    <span className={`inline-flex items-center whitespace-nowrap text-xs font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full border
      ${strong ? 'bg-foreground text-background border-foreground' : 'bg-muted text-muted-foreground border-border'}`}>
      {children}
    </span>
  );
}

/** A collapsible section: one header line (title, count, a one-line summary), collapsed by default. */
export function Section({ title, count, summary, hint, defaultOpen = false, children, right }: {
  title: string; count?: ReactNode; summary?: ReactNode; hint?: string;
  defaultOpen?: boolean; children: ReactNode; right?: ReactNode;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-t border-border first:border-t-0">
      <div className="flex items-center gap-2 py-3">
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left">
          <ChevronDown size={16} className={`shrink-0 text-muted-foreground transition-transform ${open ? '' : '-rotate-90'}`} />
          <span className="text-sm font-semibold text-foreground">{t(title)}</span>
          {count != null && <Chip strong>{count}</Chip>}
          {summary != null && !open && (
            <span className="hidden min-w-0 truncate text-xs text-muted-foreground sm:inline">{summary}</span>
          )}
        </button>
        {right}
      </div>
      {open && (
        <div className="pb-5 space-y-3">
          {hint && <p className="text-xs text-muted-foreground">{t(hint)}</p>}
          {children}
        </div>
      )}
    </section>
  );
}

export interface Col<T> {
  key: string;
  label: string;
  align?: 'left' | 'right';
  render: (row: T) => ReactNode;
  /** Shown as the card's heading on a phone instead of as a key/value line. */
  primary?: boolean;
}

/** A table at sm and up; below sm, each row is a stacked key/value card no wider than its container. */
export function StackTable<T>({ cols, rows, rowKey, empty }: {
  cols: Col<T>[]; rows: T[]; rowKey: (r: T, i: number) => string; empty?: ReactNode;
}) {
  const { t } = useI18n();
  if (rows.length === 0) return <p className="px-3 py-3 text-xs text-muted-foreground">{empty ?? t('Nothing yet.')}</p>;
  const primary = cols.filter((c) => c.primary);
  const rest = cols.filter((c) => !c.primary);
  return (
    <>
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              {cols.map((c) => (
                <th key={c.key} className={`font-medium px-3 py-2 ${c.align === 'right' ? 'text-right' : 'text-left'}`}>{t(c.label)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={rowKey(r, i)} className="border-b border-border last:border-0 align-top">
                {cols.map((c) => (
                  <td key={c.key} className={`px-3 py-1.5 ${c.align === 'right' ? 'text-right tabular-nums whitespace-nowrap' : ''}`}>{c.render(r)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="sm:hidden divide-y divide-border">
        {rows.map((r, i) => (
          <div key={rowKey(r, i)} className="px-3 py-2.5 min-w-0">
            {primary.length > 0 && (
              <div className="mb-1 text-sm font-medium text-foreground break-words">
                {primary.map((c) => <span key={c.key} className="mr-2">{c.render(r)}</span>)}
              </div>
            )}
            <dl className="grid grid-cols-[minmax(0,45%)_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
              {rest.map((c) => (
                <div key={c.key} className="contents">
                  <dt className="text-muted-foreground break-words">{t(c.label)}</dt>
                  <dd className="text-right tabular-nums break-words min-w-0">{c.render(r)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </>
  );
}

export const PAGE_SIZE = 20;

/** Client-side paging (the endpoints return every row; the page shows 20 at a time). */
export function usePaged<T>(rows: T[], resetKey: string, size = PAGE_SIZE) {
  const [page, setPage] = useState(0);
  useEffect(() => { setPage(0); }, [resetKey]);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const p = Math.min(page, pages - 1);
  return { slice: rows.slice(p * size, p * size + size), page: p, pages, setPage, total: rows.length, size };
}

export function Pager({ page, pages, total, size, setPage }: {
  page: number; pages: number; total: number; size: number; setPage: (p: number) => void;
}) {
  const { t } = useI18n();
  if (total <= size) return null;
  const from = page * size + 1;
  const to = Math.min(total, from + size - 1);
  return (
    <div className="flex items-center justify-between gap-2 px-3 py-2 border-t border-border text-xs text-muted-foreground">
      <span className="tabular-nums">{t('{from}–{to} of {total}', { from, to, total })}</span>
      <span className="flex items-center gap-1">
        <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label={t('Previous page')}
          className="p-1.5 rounded-md border border-border disabled:opacity-40 hover:bg-muted"><ChevronLeft size={14} /></button>
        <span className="tabular-nums px-1">{page + 1} / {pages}</span>
        <button type="button" disabled={page >= pages - 1} onClick={() => setPage(page + 1)} aria-label={t('Next page')}
          className="p-1.5 rounded-md border border-border disabled:opacity-40 hover:bg-muted"><ChevronRight size={14} /></button>
      </span>
    </div>
  );
}

/** Pill filter row: `[{value, label, count}]`. Scrolls sideways on a phone rather than wrapping into a wall. */
export function Pills<V extends string>({ value, options, onChange }: {
  value: V; options: { value: V; label: string; count?: number }[]; onChange: (v: V) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="flex gap-1 overflow-x-auto scrollbar-hide">
      {options.map((o) => (
        <button key={o.value} type="button" onClick={() => onChange(o.value)}
          className={`shrink-0 whitespace-nowrap text-xs px-2.5 py-1 rounded-full border tabular-nums
            ${value === o.value ? 'bg-foreground text-background border-foreground' : 'border-border text-muted-foreground hover:text-foreground'}`}>
          {t(o.label)}{o.count != null ? ` · ${o.count}` : ''}
        </button>
      ))}
    </div>
  );
}

export function ErrorCard({ what, error }: { what: string; error: string }) {
  const { t } = useI18n();
  return <Card className="p-4 text-sm text-foreground">{t('Could not load {what}: {error}', { what: t(what), error })}</Card>;
}

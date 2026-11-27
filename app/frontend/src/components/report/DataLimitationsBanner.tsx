/**
 * DataLimitationsBanner
 * ─────────────────────────────────────────────────────────────────────────────
 * Surfaces `data.report_disclosures[ticker].data_limitations` — the run's own
 * plain-English list of degraded inputs (research below the live-search floor
 * or with no cited sources, missing financial statements, failed input checks,
 * methods that could not be computed). Until this banner the list reached only
 * the PDF, so a degraded run read as clean data on the web.
 *
 * Mounted in every report render path (ReportPage, ReportViewPage desktop,
 * V2ReportView mobile) — the mobile path bypasses the desktop JSX.
 *
 * Monochrome by design (lib/semanticColors.ts): green and red are reserved for
 * price change, so the caution reads through an alert glyph, full-contrast
 * text and an elevated surface. Renders nothing when the list is empty.
 */
import { useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';
import { alertTone, emphasisTone } from '@/lib/semanticColors';
import type { ReportDisclosures } from '@/lib/reportTypes';

const COLLAPSED_COUNT = 2;

/** The run's data limitations for one ticker, [] when absent or malformed. */
function dataLimitationsFor(data: unknown, ticker: string): string[] {
  const rd = (data as { report_disclosures?: Record<string, ReportDisclosures | undefined> } | null | undefined)
    ?.report_disclosures?.[ticker];
  const list = rd?.data_limitations;
  if (!Array.isArray(list)) return [];
  return list.filter((x): x is string => typeof x === 'string' && x.trim().length > 0);
}

interface DataLimitationsBannerProps {
  /** The run's `data` payload (flat pipeline result). */
  data: unknown;
  ticker: string;
  className?: string;
}

export function DataLimitationsBanner({ data, ticker, className = '' }: DataLimitationsBannerProps) {
  const [expanded, setExpanded] = useState(false);
  const items = dataLimitationsFor(data, ticker);
  if (items.length === 0) return null;

  const shown = expanded ? items : items.slice(0, COLLAPSED_COUNT);
  const hidden = items.length - shown.length;

  return (
    <div
      role="status"
      data-testid="data-limitations-banner"
      className={`rounded-lg border border-[var(--hairline)] bg-surface-2 px-4 py-3 ${className}`}
    >
      <div className="flex items-start gap-3">
        <AlertTriangle className={`h-4 w-4 mt-0.5 shrink-0 ${alertTone('error')}`} aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className={`text-xs uppercase tracking-wider ${alertTone('error')}`}>
            Data limitations · {items.length}
          </div>
          <ul className={`mt-1 space-y-0.5 text-xs ${emphasisTone('medium')}`}>
            {shown.map((t, i) => (
              <li key={i} className="leading-snug">{t}</li>
            ))}
          </ul>
          {items.length > COLLAPSED_COUNT && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className={`mt-1 inline-flex items-center gap-1 text-xs ${emphasisTone('muted')} hover:underline`}
            >
              {expanded
                ? <>Show fewer <ChevronUp className="h-3 w-3" /></>
                : <>{hidden} more <ChevronDown className="h-3 w-3" /></>}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

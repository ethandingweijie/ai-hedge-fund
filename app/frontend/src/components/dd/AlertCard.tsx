/**
 * AlertCard.tsx — Single DD alert card.
 *
 * Visual palette mirrors src/agents/dd/slack_delivery.py PALETTE so a Slack
 * push and a dashboard card for the same event share an obvious visual
 * identity (same red for New Drop, same blue for Reversal, etc.).
 *
 * Palette (from plan section 4):
 *   New Drop      → red (#cc0000),    📉 + 🚨,  Crisis Management
 *   New Pump      → green (#1aaa55),  📈 + 🚀,  Opportunity Assessment
 *   Reversal      → blue (#3aa3e3),   🔄 + ↔,   Narrative Shift
 *   HWM Extension → purple (#800080), ⚠️ + ➖,  Compounding Risk
 */
import { useState } from 'react';
import {
  TrendingDown,
  TrendingUp,
  RefreshCw,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  ExternalLink,
} from 'lucide-react';
import type { DdAlert } from '@/lib/reportTypes';
import { parseBackendIso } from '@/lib/utils';

type Variant = 'new_drop' | 'new_pump' | 'reversal' | 'hwm_extension';

interface PaletteEntry {
  variant:    Variant;
  /** Tailwind border color (vertical stripe) */
  borderCls:  string;
  /** Tailwind background tint */
  bgCls:      string;
  /** Tailwind text color for the header tone label */
  textCls:    string;
  /** Header icon (lucide) */
  Icon:       typeof TrendingDown;
  /** Tone label shown in the header */
  tone:       string;
}

const PALETTE: Record<Variant, PaletteEntry> = {
  new_drop: {
    variant:   'new_drop',
    borderCls: 'border-l-red-600',
    bgCls:     'bg-red-50 dark:bg-red-950/20',
    textCls:   'text-red-600 dark:text-red-400',
    Icon:      TrendingDown,
    tone:      'Crisis Management',
  },
  new_pump: {
    variant:   'new_pump',
    borderCls: 'border-l-emerald-600',
    bgCls:     'bg-emerald-50 dark:bg-emerald-950/20',
    textCls:   'text-emerald-600 dark:text-emerald-400',
    Icon:      TrendingUp,
    tone:      'Opportunity Assessment',
  },
  reversal: {
    variant:   'reversal',
    borderCls: 'border-l-sky-500',
    bgCls:     'bg-sky-50 dark:bg-sky-950/20',
    textCls:   'text-sky-600 dark:text-sky-400',
    Icon:      RefreshCw,
    tone:      'Narrative Shift',
  },
  hwm_extension: {
    variant:   'hwm_extension',
    borderCls: 'border-l-purple-600',
    bgCls:     'bg-purple-50 dark:bg-purple-950/20',
    textCls:   'text-purple-600 dark:text-purple-400',
    Icon:      AlertTriangle,
    tone:      'Compounding Risk',
  },
};

/** Map (direction, alert_reason) → palette variant.
 *  Mirrors slack_delivery.py::_palette_for(). Keep in sync. */
function paletteFor(direction: string, reason: string): PaletteEntry {
  if (reason.startsWith('direction_flip'))   return PALETTE.reversal;
  if (reason.startsWith('high_water_mark'))  return PALETTE.hwm_extension;
  return direction === 'DROP' ? PALETTE.new_drop : PALETTE.new_pump;
}

interface AlertCardProps {
  alert:        DdAlert;
  /** Optional deep-link target — if provided, "Open" button navigates here.
   *  Slice version just toggles inline expand. */
  reportHref?:  string;
}

export function AlertCard({ alert }: AlertCardProps) {
  const [expanded, setExpanded] = useState(false);
  const palette = paletteFor(alert.last_direction, alert.alert_reason);
  const { Icon } = palette;
  const sign  = alert.trigger_pct >= 0 ? '+' : '';
  const pctStr = `${sign}${(alert.trigger_pct * 100).toFixed(1)}%`;
  const tsLocal = parseBackendIso(alert.last_triggered_at).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });

  return (
    <div
      className={`rounded-lg border border-border border-l-4 ${palette.borderCls} ${palette.bgCls} px-4 py-3 transition-all`}
    >
      {/* Header row — ticker + pct + tone + chevron */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center justify-between gap-3 text-left"
      >
        <div className="flex items-center gap-2 min-w-0">
          <Icon size={18} className={palette.textCls} />
          <span className="font-mono font-bold text-base text-foreground">{alert.ticker}</span>
          <span className={`font-bold text-base tabular-nums ${palette.textCls}`}>{pctStr}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={`text-[10px] uppercase tracking-wider font-semibold ${palette.textCls}`}>
            {palette.tone}
          </span>
          {expanded
            ? <ChevronUp size={16} className="text-muted-foreground" />
            : <ChevronDown size={16} className="text-muted-foreground" />}
        </div>
      </button>

      {/* Reason + metadata row — always visible */}
      <div className="mt-1.5 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
        <span className="truncate">{alert.alert_reason}</span>
        <span className="font-mono shrink-0">
          ${alert.trigger_price.toFixed(2)} · {alert.tier} · {tsLocal}
        </span>
      </div>

      {/* Expanded detail — full report content from web_runs join */}
      {expanded && alert.report && (
        <div className="mt-3 pt-3 border-t border-border/60 space-y-2 text-xs">
          {alert.report.cause_summary && (
            <div>
              <div className="text-[9px] uppercase tracking-wider font-semibold text-muted-foreground mb-0.5">Cause</div>
              <div className="text-foreground">{alert.report.cause_summary}</div>
            </div>
          )}
          {alert.report.thesis_impact && (
            <div>
              <div className="text-[9px] uppercase tracking-wider font-semibold text-muted-foreground mb-0.5">Thesis impact</div>
              <div className="text-foreground">{alert.report.thesis_impact}</div>
            </div>
          )}
          {alert.report.recommended_action && (
            <div>
              <div className="text-[9px] uppercase tracking-wider font-semibold text-muted-foreground mb-0.5">Recommended action</div>
              <div className="text-foreground">{alert.report.recommended_action}</div>
            </div>
          )}
          {alert.report.news_drivers && alert.report.news_drivers.length > 0 && (
            <div>
              <div className="text-[9px] uppercase tracking-wider font-semibold text-muted-foreground mb-0.5">News drivers</div>
              <ul className="space-y-0.5">
                {alert.report.news_drivers.slice(0, 3).map((n, i) => (
                  <li key={i} className="text-foreground">
                    {n.url ? (
                      <a href={n.url} target="_blank" rel="noopener noreferrer"
                         className="underline decoration-muted-foreground/40 hover:decoration-foreground inline-flex items-center gap-1">
                        {n.title || n.url}
                        <ExternalLink size={10} />
                      </a>
                    ) : (
                      <span>{n.title}</span>
                    )}
                    {(n.publishedDate || n.date) && (
                      <span className="text-muted-foreground ml-1">— {(n.publishedDate || n.date)?.slice(0, 10)}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {alert.report.filings && alert.report.filings.length > 0 && (
            <div>
              <div className="text-[9px] uppercase tracking-wider font-semibold text-muted-foreground mb-0.5">SEC filings</div>
              <ul className="space-y-0.5">
                {alert.report.filings.slice(0, 3).map((f, i) => (
                  <li key={i} className="text-foreground">
                    <span className="font-mono">{f.form || f.type || '?'}</span>
                    {(f.filing_date || f.date) && <span className="text-muted-foreground ml-1">{(f.filing_date || f.date)?.slice(0, 10)}</span>}
                    {f.summary && <span className="ml-1">— {f.summary}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {alert.report.insider_signal && (
            <div className="text-[10px] text-muted-foreground italic">
              insider: {alert.report.insider_signal}
            </div>
          )}
          {alert.dd_run_id && (
            <div className="text-[9px] font-mono text-muted-foreground/70 pt-1">
              run_id: {alert.dd_run_id}
            </div>
          )}
        </div>
      )}

      {/* Empty-report state — fired but no report content */}
      {expanded && !alert.report && (
        <div className="mt-3 pt-3 border-t border-border/60 text-xs text-muted-foreground italic">
          No report content available (dd_run_id not linked or web_runs row missing).
        </div>
      )}
    </div>
  );
}

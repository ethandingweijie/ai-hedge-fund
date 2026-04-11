import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { Card } from '@/components/ui/card';
import type { ValueTrapAnalysis } from '@/lib/reportTypes';

interface ValueTrapChecklistProps {
  analysis?: ValueTrapAnalysis;
  ticker: string;
}

const CHECKS = [
  { key: 'dividend_sustainability', label: 'Dividend Sustainability' },
  { key: 'structural_decline',      label: 'Structural Decline'      },
  { key: 'earnings_cash_mismatch',  label: 'Earnings / Cash Mismatch'},
  { key: 'insider_behaviour',       label: 'Insider Behaviour'       },
  { key: 'balance_sheet',           label: 'Balance Sheet'           },
] as const;

const verdictColor: Record<string, string> = {
  'TRAP RISK HIGH':   'bg-red-100   text-red-800   border-red-300   dark:bg-red-900/30   dark:text-red-300   dark:border-red-700',
  'TRAP RISK MEDIUM': 'bg-yellow-100 text-yellow-800 border-yellow-300 dark:bg-yellow-900/30 dark:text-yellow-300 dark:border-yellow-700',
  'TRAP RISK LOW':    'bg-green-100  text-green-800  border-green-300  dark:bg-green-900/30  dark:text-green-300  dark:border-green-700',
};

// Traffic-light dot colour per rating
const dotClass: Record<string, string> = {
  RED:   'bg-red-500',
  AMBER: 'bg-amber-400',
  GREEN: 'bg-green-500',
};

// Subtle row tint per rating
const rowTint: Record<string, string> = {
  RED:   'hover:bg-red-50   dark:hover:bg-red-950/20',
  AMBER: 'hover:bg-amber-50 dark:hover:bg-amber-950/20',
  GREEN: 'hover:bg-green-50 dark:hover:bg-green-950/20',
};

export function ValueTrapChecklist({ analysis, ticker }: ValueTrapChecklistProps) {
  const [openKey, setOpenKey] = useState<string | null>(null);

  if (!analysis) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">Value trap data unavailable.</p>
      </Card>
    );
  }

  const verdict    = analysis.verdict ?? analysis.overall_verdict ?? '';
  const verdictCls = verdictColor[verdict] ?? 'bg-muted text-muted-foreground border-muted';

  // Build ordered list of all checks with their rating + evidence
  const items = CHECKS.flatMap(({ key, label }) => {
    const check = analysis[key];
    if (!check) return [];
    const rating   = (check.rating ?? 'AMBER').toUpperCase() as 'RED' | 'AMBER' | 'GREEN';
    const evidence = check.evidence ?? check.detail ?? '';
    return [{ key, label, rating, evidence }];
  });

  return (
    <Card className="p-4 space-y-3">

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Value Trap Audit — {ticker}</h3>
        {verdict && (
          <span className={`text-xs px-2 py-1 rounded border font-semibold ${verdictCls}`}>
            {verdict}
          </span>
        )}
      </div>

      {/* ── Accordion rows ──────────────────────────────────────────────────── */}
      <div className="border-t divide-y divide-border/50">
        {items.map(({ key, label, rating, evidence }) => {
          const isOpen = openKey === key;
          return (
            <div key={key}>
              {/* Row trigger */}
              <button
                onClick={() => setOpenKey(isOpen ? null : key)}
                className={`w-full flex items-center gap-2.5 px-1 py-2 text-left transition-colors rounded-sm ${rowTint[rating] ?? ''}`}
              >
                {/* Traffic-light dot */}
                <span className={`shrink-0 w-2 h-2 rounded-full ${dotClass[rating] ?? 'bg-gray-400'}`} />

                {/* Label */}
                <span className="flex-1 text-xs font-medium text-foreground">
                  {label}
                </span>

                {/* Chevron */}
                <ChevronDown
                  size={13}
                  className={`shrink-0 text-muted-foreground transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
                />
              </button>

              {/* Collapsible evidence */}
              {isOpen && evidence && (
                <div className="px-5 pb-2.5 pt-0.5">
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    {evidence}
                  </p>
                </div>
              )}
            </div>
          );
        })}
      </div>

    </Card>
  );
}

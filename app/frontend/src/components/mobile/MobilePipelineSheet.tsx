import { useState } from 'react';
import { X, ChevronUp, Loader2 } from 'lucide-react';
import type { ProgressEvent } from '@/lib/reportTypes';

interface MobilePipelineSheetProps {
  isRunning: boolean;
  progress: number;
  phaseMap: Record<string, ProgressEvent>;
  totalPhases: number;
  onCancel?: () => void;
}

const PIPELINE_STEPS = [
  { key: 'macro',        label: 'Macro Regime' },
  { key: 'routing',      label: 'Router' },
  { key: 'intelligence', label: 'Intelligence' },
  { key: 'edgar',        label: 'EDGAR' },
  { key: 'data_router',  label: 'Data Router' },
  { key: 'deep_research',label: 'Deep Research' },
  { key: 'industry',     label: 'Industry' },
  { key: 'dcf',          label: 'DCF Valuation' },
  { key: 'investor',     label: 'Investor Agents' },
  { key: 'portfolio',    label: 'Portfolio Manager' },
];

function getStepStatus(key: string, phaseMap: Record<string, ProgressEvent>): 'pending' | 'running' | 'done' {
  const match = Object.entries(phaseMap).find(([phase]) => phase.toLowerCase().includes(key));
  if (!match) return 'pending';
  const ev = match[1];
  if (ev.status === 'complete' || ev.status === 'done') return 'done';
  return 'running';
}

export function MobilePipelineSheet({
  isRunning,
  progress,
  phaseMap,
  totalPhases,
  onCancel,
}: MobilePipelineSheetProps) {
  const [minimized, setMinimized] = useState(false);

  if (!isRunning) return null;

  const completedPhases = Object.values(phaseMap).filter(e => e.status === 'complete' || e.status === 'done').length;
  const pct = totalPhases > 0 ? Math.round((completedPhases / totalPhases) * 100) : Math.round(progress);

  if (minimized) {
    return (
      <div className="fixed bottom-16 left-4 right-4 z-40">
        <button
          onClick={() => setMinimized(false)}
          className="w-full bg-card border border-border rounded-xl px-4 py-2.5 flex items-center gap-3 shadow-lg"
        >
          <Loader2 size={16} className="animate-spin text-blue-500 shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="h-1.5 bg-muted rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
          <span className="text-xs font-bold text-muted-foreground shrink-0">{pct}%</span>
          <ChevronUp size={14} className="text-muted-foreground shrink-0" />
        </button>
      </div>
    );
  }

  return (
    <div className="fixed bottom-16 left-0 right-0 z-40 px-4 pb-2">
      <div className="bg-card border border-border rounded-2xl shadow-xl max-h-[45vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-card border-b border-border/50 px-4 py-3 flex items-center justify-between rounded-t-2xl">
          <div className="flex items-center gap-2">
            <Loader2 size={16} className="animate-spin text-blue-500" />
            <span className="text-sm font-semibold">Analysis Pipeline</span>
            <span className="text-xs text-muted-foreground">{pct}%</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setMinimized(true)}
              className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-muted"
            >
              <ChevronUp size={14} className="text-muted-foreground rotate-180" />
            </button>
            {onCancel && (
              <button
                onClick={onCancel}
                className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-muted"
              >
                <X size={14} className="text-muted-foreground" />
              </button>
            )}
          </div>
        </div>

        {/* Progress bar */}
        <div className="px-4 pt-3">
          <div className="h-2 bg-muted rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>

        {/* Pipeline steps */}
        <div className="px-4 py-3 space-y-2">
          {PIPELINE_STEPS.map(({ key, label }) => {
            const status = getStepStatus(key, phaseMap);
            return (
              <div key={key} className="flex items-center gap-2.5">
                {status === 'done' ? (
                  <div className="w-4 h-4 rounded-full bg-green-500 flex items-center justify-center">
                    <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                      <path d="M2 5L4 7L8 3" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </div>
                ) : status === 'running' ? (
                  <Loader2 size={16} className="animate-spin text-blue-500" />
                ) : (
                  <div className="w-4 h-4 rounded-full border-2 border-border" />
                )}
                <span className={`text-xs ${status === 'pending' ? 'text-muted-foreground' : 'text-foreground'} ${status === 'running' ? 'font-semibold' : ''}`}>
                  {label}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

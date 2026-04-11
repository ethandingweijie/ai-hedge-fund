/**
 * SectionSkeleton
 * ─────────────────────────────────────────────────────────────────────────────
 * Shown while a pipeline section is still being computed.
 * Displays: spinning circle · rotating fun phrase · live chain-of-thought
 * reasoning from the relevant SSE progress events.
 */
import { useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import type { ProgressEvent } from '@/lib/reportTypes';

// ── Rotating loading phrases ──────────────────────────────────────────────────
const PHRASES = [
  'I am cooking…',
  'I am digesting…',
  'Thinking deeply…',
  'Flipping the pages…',
  'Making magic…',
  'Crunching the numbers…',
  'Consulting the oracles…',
  'Reading between the lines…',
  'Following the money…',
  'Brewing the analysis…',
  'Running the models…',
  'Connecting the dots…',
  'Chasing the alpha…',
  'Weighing the evidence…',
  'Decoding the signals…',
];

interface SectionSkeletonProps {
  /** Section label shown as the heading */
  label: string;
  /** Latest SSE progress events relevant to this section */
  events: ProgressEvent[];
  /**
   * Pass true once the RunResult has been fetched but this section is still
   * in the stagger-reveal queue (spinner stops, shows a soft "ready" state).
   */
  resultReady?: boolean;
}

export function SectionSkeleton({ label, events, resultReady = false }: SectionSkeletonProps) {
  const [phraseIdx, setPhraseIdx] = useState(() => Math.floor(Math.random() * PHRASES.length));

  // Rotate phrase every 2.2 s while still loading
  useEffect(() => {
    if (resultReady) return;
    const id = setInterval(() => setPhraseIdx(i => (i + 1) % PHRASES.length), 2200);
    return () => clearInterval(id);
  }, [resultReady]);

  // Most recent event that has a summary and/or reasoning
  const withSummary   = [...events].reverse().find(e => e.summary);
  const withReasoning = [...events].reverse().find(e => e.reasoning?.trim());

  const phaseName = withSummary?.phase ?? '';
  const summary   = withSummary?.summary ?? '';
  const reasoning = withReasoning?.reasoning ?? '';

  return (
    <Card className="p-4 space-y-3 min-h-[100px]">

      {/* ── Spinner / check + label + rotating phrase ──────────────────────── */}
      <div className="flex items-center gap-3">
        {resultReady ? (
          /* Pulsing "ready" ring while waiting for reveal timer */
          <div className="w-5 h-5 rounded-full border-2 border-green-500/40 animate-pulse shrink-0" />
        ) : (
          <div className="w-5 h-5 rounded-full border-2 border-primary/25 border-t-primary animate-spin shrink-0" />
        )}

        <span className="text-sm font-semibold">{label}</span>

        <span className={`ml-auto text-[11px] italic transition-opacity duration-500 ${
          resultReady ? 'text-green-500/70' : 'text-muted-foreground'
        }`}>
          {resultReady ? 'Ready — loading…' : PHRASES[phraseIdx]}
        </span>
      </div>

      {/* ── Latest phase summary ────────────────────────────────────────────── */}
      {summary && !resultReady && (
        <p className="text-[11px] text-muted-foreground pl-8 leading-relaxed">
          {phaseName && (
            <span className="font-medium text-foreground/50">
              {phaseName.replace(/_/g, ' ')}
              {' — '}
            </span>
          )}
          {summary}
        </p>
      )}

      {/* ── Chain of Thought ────────────────────────────────────────────────── */}
      {reasoning && !resultReady && (
        <div className="pl-8">
          <p className="text-[9px] font-bold uppercase tracking-[0.16em] text-muted-foreground/40 mb-1.5">
            Chain of Thought
          </p>
          <pre className="text-[10px] text-muted-foreground/55 whitespace-pre-wrap leading-relaxed
                          max-h-56 overflow-y-auto bg-muted/20 rounded-md p-2.5
                          border border-border/25 font-sans scrollbar-thin">
            {reasoning}
          </pre>
        </div>
      )}

    </Card>
  );
}

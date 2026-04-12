import { Card } from '@/components/ui/card';

interface CitationAudit {
  audit_score?: number;
  hallucination_flags?: string[];
  primary_source_gaps?: string[];
}

interface CitationPanelProps {
  data?: Record<string, unknown>;
  ticker: string;
}

function scoreColor(score: number): string {
  if (score >= 8) return 'text-green-600 dark:text-green-400';
  if (score >= 5) return 'text-amber-500 dark:text-amber-400';
  return 'text-red-500 dark:text-red-400';
}


export function CitationPanel({ data, ticker }: CitationPanelProps) {
  // Citation auditor disabled on mobile/deployed app
  return null;

  const raw = (data?.citation_audit as Record<string, unknown> | undefined)?.[ticker]
    ?? data?.citation_audit;

  if (!raw) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">Citation audit unavailable for {ticker}.</p>
      </Card>
    );
  }

  if (typeof raw === 'string') {
    return (
      <Card className="p-4">
        <h3 className="text-sm font-semibold mb-3">Citation Audit — {ticker}</h3>
        <pre className="text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed bg-muted/30 p-3 rounded">
          {raw}
        </pre>
      </Card>
    );
  }

  const audit = raw as CitationAudit;
  const score              = audit.audit_score           ?? 0;
  const hallucinationFlags = audit.hallucination_flags   ?? [];
  const sourceGaps         = audit.primary_source_gaps   ?? [];

  return (
    <Card className="p-5 space-y-6">

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Citation Audit — {ticker}</h3>
        <span className={`text-sm font-bold ${scoreColor(score)}`}>
          Score {score}<span className="text-muted-foreground font-normal text-xs">/10</span>
        </span>
      </div>

      {/* ── Hallucination Flags ─────────────────────────────────────────────── */}
      {hallucinationFlags.length > 0 && (
        <section>
          <p className="text-[10px] font-bold uppercase tracking-widest text-red-500/80 mb-2">
            Hallucination Flags ({hallucinationFlags.length})
          </p>
          <ul className="space-y-1.5">
            {hallucinationFlags.map((flag, i) => (
              <li key={i} className="flex gap-2 text-xs text-foreground/80 leading-relaxed">
                <span className="text-red-500 shrink-0">⚠</span>
                <span>{flag}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ── Source Gaps ─────────────────────────────────────────────────────── */}
      {sourceGaps.length > 0 && (
        <section>
          <p className="text-[10px] font-bold uppercase tracking-widest text-amber-600/80 mb-2">
            Primary Source Gaps ({sourceGaps.length})
          </p>
          <ul className="space-y-1.5">
            {sourceGaps.map((gap, i) => (
              <li key={i} className="flex gap-2 text-xs text-foreground/80 leading-relaxed">
                <span className="text-amber-500 shrink-0">›</span>
                <span>{gap}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

    </Card>
  );
}

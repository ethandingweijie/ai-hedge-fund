import { Card } from '@/components/ui/card';
import { rankTextTone } from '@/lib/semanticColors';

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
  if (score >= 8) return rankTextTone(0);
  if (score >= 5) return rankTextTone(2);
  return rankTextTone(3);
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
        <h3 className="text-[15px] font-semibold mb-3">Citation audit</h3>
        <pre className="text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed bg-muted/30 p-3 rounded">
          {raw as string}
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
        <h3 className="text-[15px] font-semibold">Citation audit</h3>
        <span className={`text-sm font-bold ${scoreColor(score)}`}>
          Score {score}<span className="text-muted-foreground font-normal text-xs">/10</span>
        </span>
      </div>

      {/* ── Hallucination Flags ─────────────────────────────────────────────── */}
      {hallucinationFlags.length > 0 && (
        <section>
          <p className="text-[13px] font-semibold text-foreground mb-2">
            Unsupported claims ({hallucinationFlags.length})
          </p>
          <ul className="space-y-1.5">
            {hallucinationFlags.map((flag, i) => (
              <li key={i} className="flex gap-2 text-xs text-foreground/80 leading-relaxed">
                <span className="text-content-high shrink-0">⚠</span>
                <span>{flag}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ── Source Gaps ─────────────────────────────────────────────────────── */}
      {sourceGaps.length > 0 && (
        <section>
          <p className="text-[13px] font-semibold text-foreground mb-2">
            Missing primary sources ({sourceGaps.length})
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

import { Card } from '@/components/ui/card';
import type { DebateResult } from '@/lib/reportTypes';

interface DebatePanelProps {
  debateResult?: DebateResult;
  ticker: string;
}

export function DebatePanel({ debateResult, ticker }: DebatePanelProps) {
  const debate = debateResult?.[ticker];

  if (!debate || !debate.triggered) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">
          Debate round not triggered for {ticker} (requires ≥3 BUY and ≥3 SELL signals).
        </p>
      </Card>
    );
  }

  const signalColor: Record<string, string> = {
    BUY:  'bg-green-600 text-white',
    SELL: 'bg-red-600 text-white',
    HOLD: 'bg-yellow-500 text-white',
  };

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">Debate Round — {ticker}</h3>
        {debate.adjudicated_signal && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Adjudicated:</span>
            <span className={`text-xs px-2 py-0.5 rounded font-bold ${signalColor[debate.adjudicated_signal] ?? 'bg-muted text-muted-foreground'}`}>
              {debate.adjudicated_signal}
            </span>
            {debate.adjudicated_conviction != null && (
              <span className="text-xs text-muted-foreground">
                {debate.adjudicated_conviction}/10
              </span>
            )}
          </div>
        )}
      </div>

      {debate.disagreement_core && (
        <div className="mb-3 p-3 bg-muted/30 rounded text-sm">
          <span className="font-medium">Core Disagreement: </span>
          {debate.disagreement_core}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
        {debate.agent_a_rebuttal && (
          <div className="border rounded p-3">
            <p className="text-xs font-semibold text-muted-foreground mb-1">Bullish Rebuttal</p>
            <p className="text-sm">{debate.agent_a_rebuttal}</p>
          </div>
        )}
        {debate.agent_b_rebuttal && (
          <div className="border rounded p-3">
            <p className="text-xs font-semibold text-muted-foreground mb-1">Bearish Rebuttal</p>
            <p className="text-sm">{debate.agent_b_rebuttal}</p>
          </div>
        )}
      </div>

      {debate.adjudication && (
        <div className="p-3 border-l-4 border-blue-500 bg-blue-50/10 rounded-r text-sm">
          <p className="text-xs font-semibold text-blue-500 mb-1">Moderator Adjudication</p>
          <p>{debate.adjudication}</p>
        </div>
      )}
    </Card>
  );
}

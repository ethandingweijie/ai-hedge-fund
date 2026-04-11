import { Card } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { AgentSignals } from '@/lib/reportTypes';
import { useState } from 'react';
import { currencySymbol } from '@/lib/utils';

interface AgentSignalsPanelProps {
  agentSignals?: AgentSignals;
  ticker: string;
}

const signalColor: Record<string, string> = {
  BUY:   'bg-green-600 text-white',
  SELL:  'bg-red-600 text-white',
  SHORT: 'bg-orange-500 text-white',
  HOLD:  'bg-yellow-500 text-white',
  COVER: 'bg-blue-600 text-white',
};

const SKIP_AGENTS = new Set([
  'risk_management_agent',
  'advanced_risk_manager',
  'portfolio_manager_agent',
]);

function agentLabel(key: string): string {
  return key
    .replace(/_agent$/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function AgentSignalsPanel({ agentSignals, ticker }: AgentSignalsPanelProps) {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);

  if (!agentSignals) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">Agent signals unavailable.</p>
      </Card>
    );
  }

  const rows = Object.entries(agentSignals)
    .filter(([key]) => !SKIP_AGENTS.has(key))
    .map(([agentKey, byTicker]) => {
      const signal = byTicker[ticker] ?? Object.values(byTicker)[0];
      return { agentKey, signal };
    })
    // Only show true investor agents — they always carry a numeric conviction score.
    // Intelligence agents (insider_activity, news_sentiment, etc.) have no conviction
    // and belong in IntelligenceGrid instead.
    .filter(({ signal }) => signal && signal.conviction != null);

  if (rows.length === 0) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">No investor signals for {ticker}.</p>
      </Card>
    );
  }

  return (
    <Card className="p-4">
      <h3 className="text-sm font-semibold mb-3">Investor Agent Signals — {ticker}</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Agent</TableHead>
            <TableHead>Signal</TableHead>
            <TableHead className="text-right">Conviction</TableHead>
            <TableHead className="text-right">Target</TableHead>
            <TableHead>Horizon</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map(({ agentKey, signal }) => {
            const colorClass = signalColor[signal.signal] ?? 'bg-muted text-muted-foreground';
            const isExpanded = expandedAgent === agentKey;
            return (
              <>
                <TableRow
                  key={agentKey}
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => setExpandedAgent(isExpanded ? null : agentKey)}
                >
                  <TableCell className="font-medium">{agentLabel(agentKey)}</TableCell>
                  <TableCell>
                    <span className={`px-2 py-0.5 rounded text-xs font-bold ${colorClass}`}>
                      {signal.signal}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">{signal.conviction ?? '—'}/10</TableCell>
                  <TableCell className="text-right">
                    {signal.price_target != null ? `${currencySymbol(ticker)}${signal.price_target.toFixed(2)}` : '—'}
                  </TableCell>
                  <TableCell>{signal.time_horizon ?? '—'}</TableCell>
                </TableRow>
                {isExpanded && (
                  <TableRow key={`${agentKey}-detail`}>
                    <TableCell colSpan={5} className="bg-muted/30 px-4 py-3">
                      {signal.thesis_summary && (
                        <p className="text-[15px] mb-2">{signal.thesis_summary}</p>
                      )}
                      {signal.key_risks && signal.key_risks.length > 0 && (
                        <div>
                          <p className="text-xs font-semibold text-muted-foreground mb-1">Key Risks</p>
                          <ul className="text-xs text-muted-foreground list-disc list-inside">
                            {signal.key_risks.map((r, i) => <li key={i}>{r}</li>)}
                          </ul>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                )}
              </>
            );
          })}
        </TableBody>
      </Table>
    </Card>
  );
}

import { Card } from '@/components/ui/card';
import { CheckCircle } from 'lucide-react';
import { currencySymbol } from '@/lib/utils';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import type { ScenarioAnalysis } from '@/lib/reportTypes';

interface ScenarioChartProps {
  scenario?: ScenarioAnalysis;
  ticker: string;
}

export function ScenarioChart({ scenario, ticker }: ScenarioChartProps) {
  const sym = currencySymbol(ticker);
  if (!scenario) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">Scenario data unavailable.</p>
      </Card>
    );
  }

  const current = scenario.current_price ?? 0;

  // BLUF headline — computed upside/downside sentence shown before the chart
  const baseUpside = (scenario.base?.fair_value && current > 0)
    ? ((scenario.base.fair_value - current) / current * 100)
    : null;
  const bearDownside = (scenario.bear?.fair_value && current > 0)
    ? ((current - scenario.bear.fair_value) / current * 100)
    : null;
  const blufLine = (baseUpside != null && bearDownside != null)
    ? baseUpside >= 0
      ? `Base case implies +${baseUpside.toFixed(0)}% upside; bear-case downside is ${Math.abs(bearDownside).toFixed(0)}%.`
      : `Base case implies ${baseUpside.toFixed(0)}% downside; bear-case downside is ${Math.abs(bearDownside).toFixed(0)}%.`
    : baseUpside != null
    ? baseUpside >= 0
      ? `Base case implies +${baseUpside.toFixed(0)}% upside from current price.`
      : `Base case implies ${baseUpside.toFixed(0)}% downside from current price.`
    : null;

  const data = [
    {
      name: 'Bear',
      value: scenario.bear?.fair_value ?? 0,
      prob: scenario.bear?.probability ?? 0,
      color: '#ef4444',
    },
    {
      name: 'Base',
      value: scenario.base?.fair_value ?? 0,
      prob: scenario.base?.probability ?? 0,
      color: '#3b82f6',
    },
    {
      name: 'Bull',
      value: scenario.bull?.fair_value ?? 0,
      prob: scenario.bull?.probability ?? 0,
      color: '#22c55e',
    },
    {
      name: 'EV',
      value: scenario.expected_value ?? 0,
      prob: null,
      color: '#a855f7',
    },
  ];

  return (
    <Card className="p-4">
      <h3 className="text-sm font-semibold mb-1">
        Scenario Analysis — {ticker}
      </h3>
      <p className="text-xs text-muted-foreground mb-3">
        Current price: {sym}{current.toFixed(2)}
        {scenario.upside_pct != null && (
          <span className={`ml-2 font-semibold inline-flex items-center gap-1 ${scenario.upside_pct >= 0 ? 'text-green-500' : 'text-red-500'}`}>
            {scenario.upside_pct > 0 && <CheckCircle size={13} className="shrink-0" />}
            EV Upside: {scenario.upside_pct > 0 ? '+' : ''}{scenario.upside_pct.toFixed(1)}%
          </span>
        )}
      </p>
      {blufLine && (
        <p className="text-xs font-medium text-foreground/80 mb-3">{blufLine}</p>
      )}
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} barCategoryGap="30%">
          <XAxis dataKey="name" tick={{ fontSize: 12 }} />
          <YAxis
            tick={{ fontSize: 11 }}
            tickFormatter={(v: number) => `${sym}${v.toFixed(0)}`}
            domain={['auto', 'auto']}
          />
          <Tooltip
            formatter={(v, _name, entry) => [
              `${sym}${Number(v).toFixed(2)}${entry.payload.prob != null ? ` (${(entry.payload.prob * 100).toFixed(0)}%)` : ''}`,
              'Fair Value',
            ]}
          />
          {current > 0 && (
            <ReferenceLine y={current} stroke="#6b7280" strokeDasharray="4 2" label={{ value: 'Current', fontSize: 10 }} />
          )}
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </Card>
  );
}

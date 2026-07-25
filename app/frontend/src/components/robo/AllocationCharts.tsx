/**
 * AllocationCharts.tsx
 * ======================
 * Sector / geography / risk breakdown mini pie-charts for the active
 * portfolio mode. recharts is already an installed dependency (no new
 * package needed). Colors are token-derived (--brand / --primary) plus a
 * small fixed on-brand palette for additional categories, rather than raw
 * named Tailwind colors.
 */
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts';
import type { RoboBreakdowns } from '@/lib/api';

const PALETTE = [
  'hsl(var(--brand))',
  'hsl(var(--primary))',
  'hsl(96 55% 45%)',
  'hsl(96 40% 60%)',
  'hsl(150 30% 50%)',
  'hsl(150 20% 65%)',
  'hsl(40 60% 55%)',
  'hsl(20 60% 55%)',
  'hsl(280 30% 55%)',
  'hsl(0 40% 55%)',
  'hsl(200 40% 55%)',
];

function toPieData(breakdown: Record<string, number>) {
  return Object.entries(breakdown)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([name, value]) => ({ name, value: Math.round(value * 10) / 10 }));
}

function MiniPie({ title, data }: { title: string; data: { name: string; value: number }[] }) {
  if (data.length === 0) {
    return (
      <div className="flex-1 min-w-[220px] rounded-lg border border-border bg-card p-4 flex items-center justify-center text-[12px] text-muted-foreground">
        No {title.toLowerCase()} data
      </div>
    );
  }
  return (
    <div className="flex-1 min-w-[220px] rounded-lg border border-border bg-card p-4">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-muted-foreground/70 mb-2">{title}</div>
      <div className="h-[180px]">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} innerRadius={38}>
              {data.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
            </Pie>
            <Tooltip formatter={(v) => `${v}%`} />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 space-y-1">
        {data.slice(0, 6).map((d, i) => (
          <div key={d.name} className="flex items-center justify-between text-[11px] gap-2">
            <span className="flex items-center gap-1.5 text-muted-foreground truncate">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: PALETTE[i % PALETTE.length] }} />
              <span className="truncate">{d.name}</span>
            </span>
            <span className="font-mono text-foreground shrink-0">{d.value}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function AllocationCharts({ breakdowns }: { breakdowns: RoboBreakdowns }) {
  const sectorData = toPieData(breakdowns.sector);
  const geoData = toPieData(breakdowns.geography);
  const riskData = toPieData(breakdowns.risk);

  return (
    <div className="flex flex-wrap gap-3">
      <MiniPie title="Sector" data={sectorData} />
      <MiniPie title="Geography" data={geoData} />
      {riskData.length > 0 && <MiniPie title="Risk" data={riskData} />}
    </div>
  );
}

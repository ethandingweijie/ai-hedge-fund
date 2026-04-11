import type { VgpmResult } from '@/lib/reportTypes';
import { gradeColorClass } from '@/lib/gradeColors';

interface VGPMScorecardProps {
  vgpm?: VgpmResult;
}

const dimensions = [
  { key: 'valuation',    label: 'Valuation'      },
  { key: 'growth',       label: 'Growth'         },
  { key: 'profitability',label: 'Profitability'  },
  { key: 'momentum',     label: 'Momentum'       },
] as const;

export function VGPMScorecard({ vgpm }: VGPMScorecardProps) {
  if (!vgpm) {
    return <p className="text-muted-foreground text-sm">VGPM data unavailable.</p>;
  }

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {dimensions.map(({ key, label }) => {
        const dim = vgpm[key];
        if (!dim) return null;
        const colorClass = gradeColorClass(dim.grade);
        return (
          <div key={key} className="flex flex-col items-center justify-center gap-2 text-center">
            <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</span>
            <span className={`text-xl font-bold px-3 py-1 rounded-lg ${colorClass}`}>
              {dim.grade}
            </span>
          </div>
        );
      })}
    </div>
  );
}

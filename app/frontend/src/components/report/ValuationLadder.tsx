import { Card } from '@/components/ui/card';
import type { DcfRange } from '@/lib/reportTypes';
import { currencySymbol } from '@/lib/utils';

interface ValuationLadderProps {
  dcfRange?: DcfRange;
  currentPrice?: number;
  ticker: string;
}

function pct(iv: number | undefined, current: number): string {
  if (!iv || !current) return '';
  const p = ((iv - current) / current) * 100;
  return `${p > 0 ? '+' : ''}${p.toFixed(1)}%`;
}

export function ValuationLadder({ dcfRange, currentPrice, ticker }: ValuationLadderProps) {
  const sym = currencySymbol(ticker);
  if (!dcfRange) {
    return (
      <Card className="p-4">
        <p className="text-muted-foreground text-sm">DCF data unavailable for {ticker}.</p>
      </Card>
    );
  }

  const current = currentPrice ?? 0;

  const cases = [
    { label: 'Bull Case',  iv: dcfRange.bull?.intrinsic_value, growth: dcfRange.bull?.growth_rate, color: 'text-green-500' },
    { label: 'Base Case',  iv: dcfRange.base?.intrinsic_value, growth: dcfRange.base?.growth_rate, color: 'text-blue-500'  },
    { label: 'Bear Case',  iv: dcfRange.bear?.intrinsic_value, growth: dcfRange.bear?.growth_rate, color: 'text-red-500'   },
  ];

  // Compute bar widths relative to the max IV
  const maxIv = Math.max(...cases.map(c => c.iv ?? 0), current);

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold">DCF Valuation Ladder — {ticker}</h3>
        <div className="text-xs text-muted-foreground space-x-2">
          {dcfRange.wacc != null && <span>WACC: {(dcfRange.wacc * 100).toFixed(1)}%</span>}
        </div>
      </div>

      {current > 0 && (
        <div className="mb-4 flex items-center gap-2">
          <span className="text-xs text-muted-foreground w-20">Current</span>
          <div className="flex-1 bg-muted rounded h-2 relative">
            <div
              className="absolute top-0 h-2 bg-gray-400 rounded"
              style={{ width: `${(current / maxIv) * 100}%` }}
            />
          </div>
          <span className="text-xs w-20 text-right font-medium">{sym}{current.toFixed(2)}</span>
        </div>
      )}

      {cases.map(({ label, iv, growth, color }) => {
        if (!iv) return null;
        const barWidth = maxIv > 0 ? (iv / maxIv) * 100 : 0;
        const mos = pct(iv, current);
        return (
          <div key={label} className="mb-3 flex items-center gap-2">
            <span className="text-xs text-muted-foreground w-20">{label}</span>
            <div className="flex-1 bg-muted rounded h-2 relative">
              <div
                className={`absolute top-0 h-2 rounded ${color.replace('text-', 'bg-')}`}
                style={{ width: `${barWidth}%` }}
              />
            </div>
            <div className="text-xs w-32 text-right">
              <span className="font-medium">{sym}{iv.toFixed(2)}</span>
              {mos && (
                <span className={`ml-1 ${color}`}>{mos}</span>
              )}
              {growth != null && (
                <span className="text-muted-foreground ml-1">@ {(growth * 100).toFixed(0)}% g</span>
              )}
            </div>
          </div>
        );
      })}
    </Card>
  );
}

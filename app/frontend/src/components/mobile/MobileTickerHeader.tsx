import { useCompanyProfile } from '@/hooks/use-company-name';
import type { MacroRegime } from '@/lib/reportTypes';
import { currencySymbol } from '@/lib/utils';

interface MobileTickerHeaderProps {
  ticker: string;
  currentPrice?: number;
  priceChange?: number;
  regime?: MacroRegime;
  onSearchTap?: () => void;
}

export function MobileTickerHeader({
  ticker,
  currentPrice,
  priceChange,
  regime,
}: MobileTickerHeaderProps) {
  const profile = useCompanyProfile(ticker);
  const sym = currencySymbol(ticker);
  const isPositive = (priceChange ?? 0) >= 0;

  return (
    <div className="sticky top-0 z-40 bg-background/95 backdrop-blur border-b border-border pl-14 pr-14 py-2">
      <div className="flex items-center justify-between">
        {/* Left: ticker + company stacked */}
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-bold tracking-tight">{ticker}</span>
            <span className="text-xs text-muted-foreground truncate">
              {profile?.name ?? ''}
            </span>
          </div>
          {regime && (
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-muted text-muted-foreground border border-border mt-0.5">
              {regime.risk_appetite} · {regime.volatility_regime} vol
            </span>
          )}
        </div>

        {/* Right: price + change, vertically centered */}
        <div className="text-right shrink-0">
          {currentPrice != null && (
            <p className="text-base font-bold tabular-nums leading-none">
              {sym}{currentPrice.toFixed(2)}
            </p>
          )}
          {priceChange != null && (
            <p className={`text-[10px] font-semibold tabular-nums leading-tight ${isPositive ? 'text-green-500' : 'text-red-500'}`}>
              {isPositive ? '+' : ''}{priceChange.toFixed(2)}% <span className="text-muted-foreground font-normal">1Y</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

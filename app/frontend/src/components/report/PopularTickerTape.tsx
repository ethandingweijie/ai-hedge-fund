/**
 * PopularTickerTape
 * Horizontally scrolling ticker strip — transparent background, theme-aware colours.
 */
import { useEffect, useState, useRef } from 'react';
import { getPopularTickers, type PopularTicker } from '@/lib/api';
import { currencySymbol } from '@/lib/utils';

// ── CSS animation injected once ──────────────────────────────────────────────
const STYLE_ID = 'popular-ticker-tape-keyframes';
function ensureKeyframes() {
  if (document.getElementById(STYLE_ID)) return;
  const s = document.createElement('style');
  s.id = STYLE_ID;
  s.textContent = `
    @keyframes ticker-scroll {
      0%   { transform: translateX(0); }
      100% { transform: translateX(-50%); }
    }
    .ticker-tape-track {
      display: flex;
      width: max-content;
      animation: ticker-scroll 40s linear infinite;
    }
    .ticker-tape-track:hover {
      animation-play-state: paused;
    }
  `;
  document.head.appendChild(s);
}

// ── Single chip ───────────────────────────────────────────────────────────────
function TickerChip({ item, onClick }: { item: PopularTicker; onClick: (t: string) => void }) {
  const up     = item.change_pct != null && item.change_pct >= 0;
  const noData = item.price == null || item.change_pct == null;

  return (
    <button
      type="button"
      onClick={() => onClick(item.ticker)}
      className="inline-flex items-center gap-1.5 border border-white/40 hover:border-white/70 bg-white/10 hover:bg-white/20 rounded-full px-3 py-1 mx-1 cursor-pointer transition-all shrink-0"
    >
      {/* Ticker symbol */}
      <span className="font-mono font-semibold text-xs text-white tracking-wide">
        {item.ticker}
      </span>

      {!noData && (
        <>
          {/* Price */}
          <span className="text-[11px] text-white/80 font-medium">
            {currencySymbol(item.ticker)}{item.price!.toFixed(2)}
          </span>

          {/* Arrow + % change */}
          <span className={`inline-flex items-center gap-0.5 text-[11px] font-semibold ${up ? 'text-emerald-500' : 'text-red-500'}`}>
            {up ? (
              <svg className="w-2.5 h-2.5 shrink-0" viewBox="0 0 10 10" fill="currentColor">
                <path d="M5 1 L9 7 L1 7 Z" />
              </svg>
            ) : (
              <svg className="w-2.5 h-2.5 shrink-0" viewBox="0 0 10 10" fill="currentColor">
                <path d="M5 9 L9 3 L1 3 Z" />
              </svg>
            )}
            {Math.abs(item.change_pct!).toFixed(2)}%
          </span>
        </>
      )}
    </button>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
interface PopularTickerTapeProps {
  onSelect: (ticker: string) => void;
}

export function PopularTickerTape({ onSelect }: PopularTickerTapeProps) {
  const [items, setItems]     = useState<PopularTicker[]>([]);
  const [loading, setLoading] = useState(true);
  const mountedRef            = useRef(true);

  useEffect(() => {
    ensureKeyframes();
    mountedRef.current = true;
    getPopularTickers(15)
      .then(data => { if (mountedRef.current) { setItems(data); setLoading(false); } })
      .catch(() => { if (mountedRef.current) setLoading(false); });
    return () => { mountedRef.current = false; };
  }, []);

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-2">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/50 whitespace-nowrap shrink-0">
          Popular
        </span>
        <div className="flex gap-2 overflow-hidden">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-6 w-20 bg-muted/40 rounded-full animate-pulse shrink-0" />
          ))}
        </div>
      </div>
    );
  }

  if (items.length === 0) return null;

  // Double the list so the scroll loops seamlessly
  const doubled = [...items, ...items];

  return (
    <div className="space-y-1">
      <span className="text-[10px] font-semibold uppercase tracking-widest text-white/60 px-1">
        Popular
      </span>

      {/* Outer clip window — transparent, no border */}
      <div className="overflow-hidden relative py-1">
        {/* Fade edges — transparent so wallpaper shows through */}
        <div className="pointer-events-none absolute left-0 top-0 bottom-0 w-6 bg-gradient-to-r from-black/20 to-transparent z-10" />
        <div className="pointer-events-none absolute right-0 top-0 bottom-0 w-6 bg-gradient-to-l from-black/20 to-transparent z-10" />

        {/* Scrolling track */}
        <div className="ticker-tape-track">
          {doubled.map((item, i) => (
            <TickerChip key={`${item.ticker}-${i}`} item={item} onClick={onSelect} />
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * TabHero.tsx
 * ===========
 * Grab-style sticky page hero for the main tabs (Screener, Watchlist,
 * Research Ideas, Auto Due-D, History), shared by mobile and desktop.
 *
 * Behaviour modelled on Grab's Finance header (reference screenshots):
 *   - At rest: a tall deep-green band with a large bold light title
 *     (+ optional subtitle and right-aligned actions).
 *   - On scroll: the bar stays pinned (sticky) and TRANSITIONS COLOUR —
 *     green fades to the page background, the title flips to dark
 *     foreground text at the same size, the subtitle folds away, and a
 *     hairline + shadow appear so content visibly slides beneath it.
 *
 * Scroll detection uses an IntersectionObserver on a 1px sentinel rendered
 * just above the sticky band, so it works in any scroll container (the
 * desktop <main> scroller and the 430px mobile phone frame alike).
 *
 * In mobile layout mode the band pads itself below the iOS safe area and
 * right of the floating avatar button (MobileTopBar), with the title
 * flush to the normal left gutter.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { cn } from '@/lib/utils';

interface TabHeroProps {
  title: string;
  subtitle?: ReactNode;
  icon?: LucideIcon;
  /** Right-aligned slot (refresh buttons etc.). Style children for a dark
   *  background — e.g. `text-hero-foreground hover:bg-hero-foreground/10`. */
  actions?: ReactNode;
  className?: string;
}

export function TabHero({ title, subtitle, icon: Icon, actions, className }: TabHeroProps) {
  const { mode } = useLayoutMode();
  const isMobile = mode === 'mobile';
  const [scrolled, setScrolled] = useState(false);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(([e]) => setScrolled(!e.isIntersecting));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <>
      {/* 1px sentinel: when it scrolls out of view the band collapses. */}
      <div ref={sentinelRef} aria-hidden className="h-px -mb-px" />
      <div
        className={cn(
          'sticky top-0 z-30 transition-[background-color,color,box-shadow] duration-300',
          scrolled
            ? 'bg-background text-foreground shadow-sm border-b border-border/60'
            : 'bg-hero text-hero-foreground',
          className,
        )}
        // Mobile: keep the actions slot clear of the fixed avatar button
        // (top-right, viewport safe-area + 12px) while the title uses the
        // full left gutter. At rest the phone frame already pads for the
        // safe area, so we add only 12px; once pinned at the viewport top
        // the frame's padding has scrolled away, so the band must carry the
        // safe-area inset itself.
        style={isMobile ? {
          paddingTop: scrolled
            ? 'calc(env(safe-area-inset-top, 0px) + 12px)'
            : '12px',
        } : undefined}
      >
        <div
          className={cn(
            'px-4 md:px-6 flex items-center justify-between gap-3 transition-all duration-300',
            isMobile && 'pr-14', // clear the floating avatar button (top-right)
            scrolled ? 'pb-3' : 'pb-4',
            !isMobile && (scrolled ? 'pt-3' : 'pt-6'),
          )}
        >
          <div className="min-w-0">
            {/* Title keeps its size in both states (Grab keeps "Finance" large
                on the white bar too) — only the colours swap. */}
            <h1 className="font-bold tracking-tight leading-none flex items-center gap-2.5 text-[26px] md:text-[30px] min-h-9">
              {Icon && (
                <Icon
                  strokeWidth={2.5}
                  className={cn(
                    'shrink-0 w-7 h-7 md:w-8 md:h-8 transition-colors duration-300',
                    scrolled ? 'text-brand' : 'text-primary',
                  )}
                />
              )}
              <span className="truncate">{title}</span>
            </h1>
            {subtitle && (
              <p
                className={cn(
                  'overflow-hidden transition-all duration-300 text-[12.5px] md:text-sm',
                  scrolled
                    ? 'max-h-0 opacity-0 mt-0'
                    : 'max-h-12 opacity-100 mt-1.5 text-hero-foreground/70',
                )}
              >
                {subtitle}
              </p>
            )}
          </div>
          {/* Children use text-current so they follow the band's colour flip. */}
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      </div>
    </>
  );
}

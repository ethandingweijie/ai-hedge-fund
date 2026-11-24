/**
 * TabHero.tsx
 * ===========
 * Sticky page header for the main tabs (Screener, Watchlist, Portfolio,
 * History, Research Ideas, Discuss, Robo, Model Accuracy), shared by mobile
 * and desktop.
 *
 * Owner, 2026-11-24 (UI clean-up, "Robinhood-style"): a large bold title on
 * the page background — no coloured band, no icon, no subtitle line. The
 * `icon` and `subtitle` props are still accepted so call sites keep
 * compiling, but neither is rendered: the subtitles were the wordy
 * "explain the page" captions the clean-up removes. A subtitle that carries
 * real data should move into the page body.
 *
 * On scroll the header stays pinned, shrinks a step and gains a hairline so
 * content visibly slides beneath it. Scroll detection uses an
 * IntersectionObserver on a 1px sentinel above the sticky block, so it works
 * in any scroll container (desktop <main> and the mobile phone frame alike).
 *
 * In mobile layout mode the header pads itself below the iOS safe area and
 * clears the floating avatar button (MobileTopBar, top-right).
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { cn } from '@/lib/utils';
import { useI18n } from '@/i18n';

interface TabHeroProps {
  title: string;
  /** Accepted for compatibility; not rendered (see file header). */
  subtitle?: ReactNode;
  /** Accepted for compatibility; not rendered (see file header). */
  icon?: LucideIcon;
  /** Right-aligned slot (refresh buttons etc.). */
  actions?: ReactNode;
  className?: string;
}

export function TabHero({ title, actions, className }: TabHeroProps) {
  const { t } = useI18n();
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

  // Call sites style their action buttons for the old dark band
  // (`text-hero-foreground hover:bg-hero-foreground/10`). Point those tokens
  // at the page colours here so the same classes read correctly in both
  // themes without touching every page.
  const tokenRemap = {
    '--hero': 'var(--background)',
    '--hero-foreground': 'var(--foreground)',
  } as CSSProperties;

  return (
    <>
      <div ref={sentinelRef} aria-hidden className="h-px -mb-px" />
      <div
        className={cn(
          'sticky top-0 z-30 bg-background text-foreground transition-[box-shadow,border-color] duration-200',
          scrolled ? 'border-b border-border' : 'border-b border-transparent',
          className,
        )}
        style={{
          ...tokenRemap,
          ...(isMobile ? {
            paddingTop: scrolled ? 'calc(env(safe-area-inset-top, 0px) + 12px)' : '12px',
          } : {}),
        }}
      >
        <div
          className={cn(
            'px-4 md:px-6 flex items-center justify-between gap-3 transition-all duration-200',
            isMobile && 'pr-14', // clear the floating avatar button (top-right)
            scrolled ? 'pb-3' : 'pb-5',
            !isMobile && (scrolled ? 'pt-3' : 'pt-8'),
          )}
        >
          <h1
            className={cn(
              'min-w-0 truncate font-bold tracking-tight leading-none transition-all duration-200',
              scrolled ? 'text-[22px] md:text-[24px]' : 'text-[32px] md:text-[36px]',
            )}
            style={{ letterSpacing: '-0.02em' }}
          >
            {t(title)}
          </h1>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      </div>
    </>
  );
}

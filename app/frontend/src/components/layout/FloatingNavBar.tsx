/**
 * FloatingNavBar.tsx
 * ==================
 * Common floating bottom bar, rendered on every authenticated screen in both
 * shells (mounted by MobileLayout / DesktopLayout). Replaces the old
 * home-page squircle grid: the five primary destinations are always one tap
 * away instead of only on the main page. Auto Due-D stays in the
 * sidebar/drawer nav.
 *
 *   • mobile mode  — full-width floating pill aligned to the 430px phone frame
 *   • desktop mode — compact centred dock
 *
 * Sits above env(safe-area-inset-bottom) so PWA iPhones clear the home
 * indicator. z-50: below the hamburger (z-10000) and drawer (z-10001).
 */
import { Filter, Lightbulb, BookMarked, Wallet, History } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useLayoutMode } from '@/contexts/layout-mode-context';

const ITEMS = [
  { label: 'Screener',  icon: Filter,     path: '/screener' },
  { label: 'Research',  icon: Lightbulb,  path: '/research-ideas' },
  { label: 'Watchlist', icon: BookMarked, path: '/watchlist' },
  { label: 'Robo',      icon: Wallet,     path: '/robo-strategy' },
  { label: 'History',   icon: History,    path: '/history' },
];

export function FloatingNavBar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { mode } = useLayoutMode();

  return (
    <nav
      className={`fixed left-1/2 -translate-x-1/2 z-50 ${
        mode === 'mobile' ? 'w-[calc(100%-24px)] max-w-[406px]' : ''
      }`}
      style={{ bottom: 'calc(env(safe-area-inset-bottom, 0px) + 12px)' }}
    >
      <div
        className={`flex items-stretch justify-around rounded-2xl border border-border/70 bg-card/90 backdrop-blur-md
          shadow-[0_12px_32px_rgb(0_0_0/0.12),0_2px_8px_rgb(0_0_0/0.08)] py-1.5 ${
          mode === 'mobile' ? 'px-1' : 'px-2'
        }`}
      >
        {ITEMS.map(({ icon: Icon, label, path }) => {
          const active = pathname === path || pathname.startsWith(path + '/');
          return (
            <button
              key={path}
              type="button"
              onClick={() => navigate(path)}
              className={`flex flex-col items-center justify-center gap-1 rounded-xl transition-colors ${
                mode === 'mobile' ? 'flex-1 py-1.5' : 'px-4 py-1.5'
              } ${
                active
                  ? 'text-brand bg-brand/10'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
              }`}
            >
              <Icon size={19} strokeWidth={active ? 2.2 : 1.8} />
              <span className="text-[10px] font-medium leading-none">{label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

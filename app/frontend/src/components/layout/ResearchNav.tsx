import { useState } from 'react';
import { BarChart2, Filter, History, BookMarked, Zap, Sun, Moon, Monitor, User } from 'lucide-react';
import { useTheme, type Theme } from '@/contexts/theme-context';
import { useAuth } from '@/contexts/auth-context';
import { useIsMobile } from '@/hooks/use-mobile';
import { MobileProfileDrawer } from '@/components/mobile/MobileProfileDrawer';

const TABS = [
  { label: 'Ticker Research', icon: BarChart2,   href: '#/report'    },
  { label: 'Stock Screener',  icon: Filter,      href: '#/screener'  },
  { label: 'Watchlist',       icon: BookMarked,  href: '#/watchlist' },
  { label: 'History',         icon: History,     href: '#/history'   },
] as const;

const THEME_ICONS: Record<Theme, typeof Sun> = { light: Sun, dark: Moon, auto: Monitor };
const NEXT_THEME: Record<Theme, Theme> = { light: 'dark', dark: 'auto', auto: 'light' };
const THEME_LABEL: Record<Theme, string> = { light: 'Light', dark: 'Dark', auto: 'Auto' };

export function ResearchNav() {
  const hash = window.location.hash.split('?')[0];
  const active = TABS.find(t => hash.startsWith(t.href)) ?? TABS[0];
  const { theme, setTheme } = useTheme();
  const ThemeIcon = THEME_ICONS[theme];
  const { user, logout } = useAuth();
  const isMobile = useIsMobile();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Mobile: top bar handled by MobileTopBar in MobileLayout — hide desktop nav
  if (isMobile) {
    return null;
  }

  // Desktop: full navigation
  return (
    <div className="sticky top-0 z-50 bg-background border-b border-border">
      <div className="max-w-6xl mx-auto px-4 md:px-8 grid grid-cols-[1fr_auto_1fr] items-center">
        <div>{/* spacer */}</div>
        <nav className="flex items-center gap-1">
          {TABS.map(({ label, icon: Icon, href }) => {
            const isActive = active.href === href;
            return (
              <a
                key={href}
                href={href}
                className={`
                  flex items-center gap-2 px-4 py-3 text-[17px] font-medium
                  border-b-2 transition-colors
                  ${isActive
                    ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                    : 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'
                  }
                `}
              >
                <Icon size={17} />
                {label}
              </a>
            );
          })}
        </nav>

        <div className="flex items-center gap-1 justify-end">
          {/* Pricing */}
          <a
            href="#/pricing"
            className={`
              flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors
              ${hash.startsWith('#/pricing')
                ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              }
            `}
          >
            <Zap size={13} />
            Pricing
          </a>

          {/* Dark mode cycle: light → dark → auto → light */}
          <button
            onClick={() => setTheme(NEXT_THEME[theme])}
            title={`Theme: ${THEME_LABEL[theme]}`}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          >
            <ThemeIcon size={13} />
            {THEME_LABEL[theme]}
          </button>

          {/* Profile icon */}
          {user ? (
            <div className="relative group ml-1">
              {user.avatar_url ? (
                <img
                  src={user.avatar_url}
                  alt={user.name ?? user.email}
                  className="w-8 h-8 rounded-full object-cover ring-2 ring-border cursor-pointer hover:ring-primary/60 transition-all"
                />
              ) : (
                <div className="w-8 h-8 rounded-full bg-primary/15 flex items-center justify-center text-xs font-bold text-primary cursor-pointer ring-2 ring-border hover:ring-primary/60 transition-all">
                  {(user.name ?? user.email)[0].toUpperCase()}
                </div>
              )}
              {/* Dropdown */}
              <div className="absolute right-0 top-10 w-48 bg-background border border-border rounded-2xl shadow-xl py-2 opacity-0 group-hover:opacity-100 pointer-events-none group-hover:pointer-events-auto transition-all duration-200 z-50">
                <div className="px-4 py-2 border-b border-border/50">
                  <p className="text-sm font-semibold truncate">{user.name ?? ''}</p>
                  <p className="text-xs text-muted-foreground truncate">{user.email}</p>
                </div>
                <button
                  onClick={logout}
                  className="w-full text-left px-4 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
                >
                  Sign out
                </button>
              </div>
            </div>
          ) : (
            <a
              href="#/login"
              title="Sign in"
              className="ml-1 w-8 h-8 rounded-full bg-muted flex items-center justify-center ring-2 ring-border hover:ring-primary/60 hover:bg-muted/80 transition-all"
            >
              <svg className="w-4 h-4 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z" />
              </svg>
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

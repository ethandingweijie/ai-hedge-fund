/**
 * DesktopSidebar.tsx
 * ==================
 * Persistent left navigation rail for the desktop / iPad shell. Reuses the
 * shared NAV_ITEMS + useAppNav (same nav semantics as the mobile drawer) and
 * the shared Theme / Layout-mode toggles.
 *
 * Collapse behaviour: a chevron toggles between a full 240px rail (icon +
 * label + bottom settings cluster) and a 64px icon-only rail (icons + compact
 * single-button controls). State is owned by the parent (DesktopLayout) so it
 * can persist the preference.
 */
import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import {
  User, LogOut, Zap, Sun, Moon, Monitor, Smartphone,
  ChevronsLeft, ChevronsRight,
} from 'lucide-react';
import { useAuth } from '@/contexts/auth-context';
import { useTheme } from '@/contexts/theme-context';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { getHistory } from '@/lib/api';
import { parseBackendIso } from '@/lib/utils';
import type { RunSummary } from '@/lib/reportTypes';
import { NAV_ITEMS, useAppNav, type NavItem } from '@/components/nav-config';
import { LayoutModeToggle } from '@/components/LayoutModeToggle';
import { ThemeToggle } from '@/components/ThemeToggle';
import { EquitableMark } from '@/components/brand/EquitableMark';

const ACTION_COLORS: Record<string, string> = {
  BUY:   'bg-green-600 text-white',
  SELL:  'bg-red-600 text-white',
  SHORT: 'bg-orange-500 text-white',
  COVER: 'bg-blue-600 text-white',
  HOLD:  'bg-yellow-500 text-white',
};

const THEME_ICON = { light: Sun, dark: Moon, auto: Monitor } as const;

interface DesktopSidebarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export function DesktopSidebar({ collapsed, onToggleCollapse }: DesktopSidebarProps) {
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const { toggle: toggleLayout } = useLayoutMode();
  const location = useLocation();
  const handleNav = useAppNav();
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);

  // Recent runs (mirrors the mobile drawer). Fetched once on mount; cheap and
  // gives the desktop rail a useful "jump back into a run" affordance.
  useEffect(() => {
    getHistory({ page: 1, page_size: 5 })
      .then((res) => setRecentRuns(res.items.slice(0, 5)))
      .catch(() => {});
  }, []);

  const isActive = (item: NavItem) => {
    if (item.action === 'new') return false; // "New Analysis" never shows as active
    return location.pathname === item.path || location.pathname.startsWith(item.path + '/');
  };

  const cycleTheme = () =>
    setTheme(theme === 'light' ? 'dark' : theme === 'dark' ? 'auto' : 'light');

  const ThemeIcon = THEME_ICON[theme];

  return (
    <aside
      className={`shrink-0 h-full flex flex-col border-r border-border bg-card/40
        transition-[width] duration-200 ${collapsed ? 'w-16' : 'w-60'}`}
    >
      {/* ── Header: logo + collapse toggle ─────────────────────────────────── */}
      <div className={`flex items-center border-b border-border h-14 ${collapsed ? 'justify-center px-0' : 'justify-between px-3'}`}>
        {!collapsed && (
          <a href="#/report" className="flex items-center gap-2 min-w-0">
            <EquitableMark size={28} className="shrink-0" />
            <span className="text-sm font-bold tracking-wide text-foreground truncate">Equitable</span>
          </a>
        )}
        <button
          onClick={onToggleCollapse}
          className="p-1.5 rounded-md hover:bg-muted text-muted-foreground"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
        </button>
      </div>

      {/* ── Nav items ──────────────────────────────────────────────────────── */}
      <nav className="flex-1 overflow-y-auto py-2 px-2 space-y-0.5">
        {NAV_ITEMS.map((item) => {
          const { label, icon: Icon, hint } = item;
          const active = isActive(item);
          return (
            <button
              key={label}
              onClick={() => handleNav(item)}
              title={collapsed ? label : hint}
              className={`w-full flex items-center rounded-md transition-colors
                ${collapsed ? 'justify-center px-0 py-2.5' : 'gap-3 px-3 py-2'}
                ${active
                  ? 'bg-primary/10 text-primary'
                  : 'text-foreground/80 hover:bg-muted hover:text-foreground'
                }`}
            >
              <Icon size={18} strokeWidth={active ? 2.2 : 1.7} className={active ? 'text-primary shrink-0' : 'text-muted-foreground shrink-0'} />
              {!collapsed && <span className={`text-sm font-medium truncate ${active ? 'text-primary' : ''}`}>{label}</span>}
            </button>
          );
        })}

        {/* Recent runs — expanded only */}
        {!collapsed && recentRuns.length > 0 && (
          <div className="pt-3 mt-1 border-t border-border/60">
            <span className="block px-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-1">Recent</span>
            {recentRuns.map((run) => (
              <a
                key={run.run_id}
                href={`#/report/${run.run_id}`}
                className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md hover:bg-muted transition-colors"
              >
                <span className="font-mono text-xs font-bold text-foreground min-w-[44px]">{run.ticker}</span>
                {run.final_action && (
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold leading-none ${ACTION_COLORS[run.final_action] ?? 'bg-muted text-muted-foreground'}`}>
                    {run.final_action}
                  </span>
                )}
                <span className="ml-auto text-[10px] text-muted-foreground/50 font-mono">
                  {parseBackendIso(run.run_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                </span>
              </a>
            ))}
          </div>
        )}
      </nav>

      {/* ── Bottom settings cluster ────────────────────────────────────────── */}
      <div className="border-t border-border">
        {collapsed ? (
          /* Compact icon-only controls when the rail is collapsed */
          <div className="flex flex-col items-center gap-1 py-2">
            <button
              onClick={toggleLayout}
              className="p-2 rounded-md hover:bg-muted text-muted-foreground"
              title="Switch to mobile layout"
              aria-label="Switch to mobile layout"
            >
              <Smartphone size={18} />
            </button>
            <button
              onClick={cycleTheme}
              className="p-2 rounded-md hover:bg-muted text-muted-foreground"
              title={`Theme: ${theme} (click to cycle)`}
              aria-label="Cycle theme"
            >
              <ThemeIcon size={18} />
            </button>
            <a
              href="#/pricing"
              className="p-2 rounded-md hover:bg-muted text-amber-500"
              title="Pricing"
              aria-label="Pricing"
            >
              <Zap size={18} />
            </a>
            {user && (
              <button
                onClick={logout}
                className="p-2 rounded-md hover:bg-muted text-muted-foreground"
                title="Sign out"
                aria-label="Sign out"
              >
                <LogOut size={18} />
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="px-3 py-3 border-b border-border/60">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-2">Layout</p>
              <LayoutModeToggle />
            </div>
            <div className="px-3 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-2">Theme</p>
              <ThemeToggle />
            </div>
            <a
              href="#/pricing"
              className="flex items-center gap-3 px-3 py-2.5 hover:bg-muted transition-colors"
            >
              <Zap size={16} className="text-amber-500" />
              <span className="text-sm font-medium">Pricing</span>
            </a>
            {user && (
              <button
                onClick={logout}
                className="w-full flex items-center gap-3 px-3 py-2.5 hover:bg-muted transition-colors text-left"
              >
                <LogOut size={16} className="text-muted-foreground" />
                <span className="text-sm font-medium text-muted-foreground">Sign out</span>
              </button>
            )}
            {user && (
              <div className="px-3 py-3 border-t border-border">
                <div className="flex items-center gap-2">
                  {user.avatar_url ? (
                    <img src={user.avatar_url} alt="" className="w-7 h-7 rounded-full object-cover shrink-0" />
                  ) : (
                    <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center shrink-0">
                      <User size={14} className="text-muted-foreground" />
                    </div>
                  )}
                  <div className="flex flex-col min-w-0">
                    <span className="text-xs font-medium truncate">{user.name ?? user.email}</span>
                    <span className="text-[10px] text-muted-foreground truncate">{user.email}</span>
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </aside>
  );
}

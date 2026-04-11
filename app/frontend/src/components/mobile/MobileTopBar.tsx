import { useState, useEffect } from 'react';
import { Menu, X, User, Plus, BarChart2, Filter, BookMarked, History } from 'lucide-react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';
// useActiveRun not needed — "New Ticker" passes state flag, ReportPage handles the rest
import { MobileProfileDrawer } from './MobileProfileDrawer';
import { getHistory } from '@/lib/api';
import type { RunSummary } from '@/lib/reportTypes';

const ACTION_COLORS: Record<string, string> = {
  BUY:   'bg-green-600 text-white',
  SELL:  'bg-red-600 text-white',
  SHORT: 'bg-orange-500 text-white',
  COVER: 'bg-blue-600 text-white',
  HOLD:  'bg-yellow-500 text-white',
};

const NAV_ITEMS = [
  { label: 'New Ticker',       icon: Plus,       path: '/report'   },
  { label: 'Ticker Research',  icon: BarChart2,  path: '/report'   },
  { label: 'Screener',         icon: Filter,     path: '/screener' },
  { label: 'Watchlist',        icon: BookMarked, path: '/watchlist' },
  { label: 'History',          icon: History,    path: '/history'  },
] as const;

export function MobileTopBar() {
  const { user } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);
  const location = useLocation();
  const navigate = useNavigate();

  // Fetch last 5 runs when menu opens
  useEffect(() => {
    if (!menuOpen) return;
    getHistory({ page: 1, page_size: 5 })
      .then((res) => setRecentRuns(res.items.slice(0, 5)))
      .catch(() => {});
  }, [menuOpen]);

  const handleNav = (label: string, path: string) => {
    setMenuOpen(false);
    if (label === 'New Ticker') {
      // Unique query param forces React Router to remount ReportPage even if already on /report
      navigate(`${path}?new=${Date.now()}`, { state: { fresh: true } });
    } else if (label === 'Ticker Research') {
      navigate(path, { state: { resume: true } });
    } else {
      navigate(path);
    }
  };

  const handleRecentClick = (runId: string) => {
    setMenuOpen(false);
    navigate(`/report/${runId}`);
  };

  return (
    <>
      {/* Hamburger menu — top-left */}
      <div className="absolute top-3 left-3 z-[60]">
        <button
          onClick={() => setMenuOpen(true)}
          className="w-9 h-9 rounded-full flex items-center justify-center shadow-md bg-white/90 dark:bg-card border border-border"
        >
          <Menu size={18} className="text-foreground" />
        </button>
      </div>

      {/* Profile icon — top-right */}
      <div className="absolute top-3 right-3 z-[60]">
        <button
          onClick={() => setDrawerOpen(true)}
          className="w-9 h-9 rounded-full flex items-center justify-center shadow-md bg-white/90 dark:bg-card border border-border"
        >
          {user?.avatar_url ? (
            <img
              src={user.avatar_url}
              alt={user.name ?? user.email}
              className="w-8 h-8 rounded-full object-cover"
            />
          ) : (
            <User size={16} className="text-muted-foreground" />
          )}
        </button>
      </div>

      {/* Navigation drawer — slides from left */}
      {menuOpen && (
        <div className="fixed inset-0 z-[70]" onClick={() => setMenuOpen(false)}>
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/50 animate-in fade-in duration-200" />

          {/* Drawer panel */}
          <div
            className="absolute top-0 left-0 bottom-0 w-64 bg-background border-r border-border shadow-2xl animate-in slide-in-from-left duration-200 flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Drawer header */}
            <div className="flex items-center justify-between px-4 py-4 border-b border-border">
              <span className="text-sm font-bold tracking-wide text-foreground">AI Hedge Fund</span>
              <button onClick={() => setMenuOpen(false)} className="p-1 rounded-md hover:bg-muted">
                <X size={18} className="text-muted-foreground" />
              </button>
            </div>

            {/* Nav items */}
            <div className="flex-1 overflow-y-auto py-2">
              {NAV_ITEMS.map(({ label, icon: Icon, path }) => {
                const isActive = location.pathname === path && label !== 'New Ticker';
                const isHistory = label === 'History';
                return (
                  <div key={label}>
                    <button
                      onClick={() => handleNav(label, path)}
                      className={`w-full flex items-center gap-3 px-4 py-3 text-left transition-colors
                        ${isActive
                          ? 'bg-primary/10 text-primary border-l-2 border-primary'
                          : 'text-foreground hover:bg-muted border-l-2 border-transparent'
                        }
                        ${label === 'New Ticker' ? 'border-b border-border mb-1' : ''}`}
                    >
                      <Icon size={18} strokeWidth={isActive ? 2.2 : 1.6} className={isActive ? 'text-primary' : 'text-muted-foreground'} />
                      <span className={`text-sm font-medium ${isActive ? 'text-primary' : ''}`}>{label}</span>
                    </button>

                    {/* Recent runs below History */}
                    {isHistory && recentRuns.length > 0 && (
                      <div className="pl-10 pr-4 py-1 space-y-0.5">
                        <span className="text-[9px] font-semibold uppercase tracking-wider text-muted-foreground/60 px-1">Recent</span>
                        {recentRuns.map((run) => (
                          <button
                            key={run.run_id}
                            onClick={() => handleRecentClick(run.run_id)}
                            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-muted transition-colors text-left"
                          >
                            <span className="font-mono text-xs font-bold text-foreground min-w-[48px]">{run.ticker}</span>
                            {run.final_action && (
                              <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold leading-none ${ACTION_COLORS[run.final_action] ?? 'bg-muted text-muted-foreground'}`}>
                                {run.final_action}
                              </span>
                            )}
                            <span className="ml-auto text-[9px] text-muted-foreground/50 font-mono">
                              {new Date(run.run_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {/* User info at bottom */}
            {user && (
              <div className="px-4 py-3 border-t border-border">
                <div className="flex items-center gap-2">
                  {user.avatar_url ? (
                    <img src={user.avatar_url} alt="" className="w-7 h-7 rounded-full object-cover" />
                  ) : (
                    <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center">
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
          </div>
        </div>
      )}

      <MobileProfileDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </>
  );
}

/**
 * MenuPage.tsx
 * ============
 * Full-page menu opened from the floating avatar (top-right) in the mobile
 * shell. Replaces the old hamburger drawer: it's a route, not an overlay, so
 * back-swipe / back-button are native and there's no z-index fight with the
 * toaster (the hamburger used to sit at z-10000 purely to outrank it).
 *
 * Sectioned by intent — workflow first, identity chrome last:
 *   ANALYZE     New / Current Analysis + recent runs
 *   EXPLORE     long-tail destinations not on the floating bottom bar
 *   PREFERENCES theme, layout mode, sign out
 *
 * Mounted in both shells (desktop reaches it via direct URL); the desktop
 * sidebar already surfaces the same items, so this page is mostly the mobile
 * surface.
 */
import { useEffect, useState } from 'react';
import {
  ChevronLeft, Plus, BarChart2, MessageSquare, Zap, LogOut,
  Sun, Moon, Monitor, User, PieChart, type LucideIcon,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';
import { useTheme, type Theme } from '@/contexts/theme-context';
import { getHistory } from '@/lib/api';
import { parseBackendIso } from '@/lib/utils';
import type { RunSummary } from '@/lib/reportTypes';
import { NAV_ITEMS, useAppNav } from '@/components/nav-config';
import { LayoutModeToggle } from '@/components/LayoutModeToggle';
import { actionTone } from '@/lib/semanticColors';


const THEMES: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: 'light', icon: Sun,     label: 'Light' },
  { value: 'dark',  icon: Moon,    label: 'Dark'  },
  { value: 'auto',  icon: Monitor, label: 'Auto'  },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 px-1 mb-2">
      {children}
    </p>
  );
}

/** One card = one section; rows separated by hairlines. */
function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-border/70 bg-card shadow-[0_1px_2px_rgb(0_0_0/0.04),0_2px_10px_rgb(0_0_0/0.04)] overflow-hidden divide-y divide-border/60">
      {children}
    </div>
  );
}

function Row({ icon: Icon, label, onClick, muted = false }: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  muted?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/60 transition-colors"
    >
      <Icon size={18} strokeWidth={1.8} className={muted ? 'text-muted-foreground' : 'text-muted-foreground'} />
      <span className={`text-sm font-medium ${muted ? 'text-muted-foreground' : 'text-foreground'}`}>{label}</span>
    </button>
  );
}

export function MenuPage() {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const handleNav = useAppNav();
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);

  useEffect(() => {
    getHistory({ page: 1, page_size: 5 })
      .then((res) => setRecentRuns(res.items.slice(0, 5)))
      .catch(() => {});
  }, []);

  // The two ReportPage entry points and the long-tail destinations live in
  // NAV_ITEMS; look them up by stable keys (action / path), not labels.
  const byAction = (a: 'new' | 'resume') => NAV_ITEMS.find((i) => i.action === a)!;
  const byPath = (p: string) => NAV_ITEMS.find((i) => i.path === p)!;

  const initial = (user?.name ?? user?.email ?? '?')[0]?.toUpperCase() ?? '?';

  return (
    <div className="min-h-full bg-background">
      {/* pt-16 clears the floating avatar (mobile); desktop gets normal py. */}
      <div className="mx-auto w-full max-w-3xl px-4 md:px-8 pt-16 pb-10 md:pt-8 space-y-6">
        <button
          onClick={() => navigate(-1)}
          aria-label="Back"
          className="w-9 h-9 rounded-full flex items-center justify-center border border-border/70 bg-card shadow-sm hover:bg-muted transition-colors"
        >
          <ChevronLeft size={18} className="text-foreground" />
        </button>

        {/* Identity header */}
        <div className="flex items-center gap-4">
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt="" className="w-16 h-16 rounded-full object-cover ring-2 ring-border" />
          ) : (
            <div className="w-16 h-16 rounded-full bg-primary/15 flex items-center justify-center text-xl font-bold text-primary ring-2 ring-border">
              {initial}
            </div>
          )}
          <div className="min-w-0">
            <p className="text-lg font-semibold text-foreground truncate">{user?.name ?? 'Guest'}</p>
            <p className="text-sm text-muted-foreground truncate">{user?.email}</p>
          </div>
        </div>

        <section>
          <SectionLabel>Analyze</SectionLabel>
          <Card>
            <Row icon={Plus} label="New Analysis" onClick={() => handleNav(byAction('new'))} />
            <Row icon={BarChart2} label="Current Analysis" onClick={() => handleNav(byAction('resume'))} />
            {recentRuns.length > 0 && (
              <div className="px-4 py-2">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-1">Recent</p>
                <div className="space-y-0.5">
                  {recentRuns.map((run) => (
                    <button
                      key={run.run_id}
                      onClick={() => navigate(`/report/${run.run_id}`)}
                      className="w-full flex items-center gap-2 px-2 py-2 rounded-lg hover:bg-muted/60 transition-colors text-left"
                    >
                      <span className="font-mono text-xs font-bold text-foreground min-w-[48px]">{run.ticker}</span>
                      {run.final_action && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold leading-none ${actionTone(run.final_action)}`}>
                          {run.final_action}
                        </span>
                      )}
                      <span className="ml-auto text-[10px] text-muted-foreground/50 font-mono">
                        {parseBackendIso(run.run_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </section>

        <section>
          <SectionLabel>Explore</SectionLabel>
          <Card>
            <Row icon={PieChart} label="Portfolio" onClick={() => handleNav(byPath('/portfolio'))} />
            <Row icon={MessageSquare} label="Discuss" onClick={() => handleNav(byPath('/discuss'))} />
            <Row icon={Zap} label="Pricing" onClick={() => navigate('/pricing')} />
          </Card>
        </section>

        <section>
          <SectionLabel>Preferences</SectionLabel>
          <Card>
            <div className="px-4 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-2">Theme</p>
              <div className="flex gap-2">
                {THEMES.map(({ value, icon: Icon, label }) => (
                  <button
                    key={value}
                    onClick={() => setTheme(value)}
                    className={`flex-1 flex flex-col items-center gap-1.5 py-2.5 rounded-lg transition-colors
                      ${theme === value
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted text-muted-foreground hover:text-foreground'
                      }`}
                  >
                    <Icon size={16} />
                    <span className="text-[10px] font-medium">{label}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="px-4 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 mb-2">Layout</p>
              <LayoutModeToggle />
            </div>
            <Row icon={LogOut} label="Sign out" muted onClick={() => logout()} />
          </Card>
        </section>

        {/* Fallback identity row for screen readers / empty states */}
        {!user && (
          <p className="text-xs text-muted-foreground flex items-center gap-2">
            <User size={14} /> Not signed in.
          </p>
        )}
      </div>
    </div>
  );
}

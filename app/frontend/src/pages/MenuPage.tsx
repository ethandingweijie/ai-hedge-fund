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
  Sun, Moon, Monitor, User, PieChart, BookMarked, Newspaper, FileText, Trash2, type LucideIcon,
} from 'lucide-react';
import { deleteAccount } from '@/lib/api';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';
import { useTheme, type Theme } from '@/contexts/theme-context';
import { getHistory } from '@/lib/api';
import { parseBackendIso } from '@/lib/utils';
import type { RunSummary } from '@/lib/reportTypes';
import { NAV_ITEMS, useAppNav } from '@/components/nav-config';
import { LanguageToggle } from '@/components/LanguageToggle';
import { LayoutModeToggle } from '@/components/LayoutModeToggle';
import { actionTone } from '@/lib/semanticColors';
import { useI18n } from '@/i18n';


const THEMES: { value: Theme; icon: typeof Sun; label: string }[] = [
  { value: 'light', icon: Sun,     label: 'Light' },
  { value: 'dark',  icon: Moon,    label: 'Dark'  },
  { value: 'auto',  icon: Monitor, label: 'Auto'  },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <p className="text-xs font-semibold text-muted-foreground/60 px-1 mb-2">
      {typeof children === 'string' ? t(children) : children}
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

/** PDPA self-service deletion: a typed confirmation, then DELETE /auth/me. */
function DeleteAccountRow({ onDeleted }: { onDeleted: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!open) return <Row icon={Trash2} label="Delete account" muted onClick={() => setOpen(true)} />;
  const go = async () => {
    setBusy(true); setErr(null);
    try { await deleteAccount(); onDeleted(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); setBusy(false); }
  };
  return (
    <div className="px-4 py-3 space-y-2">
      <p className="text-sm font-semibold text-foreground">{t('Delete your account?')}</p>
      <p className="text-xs text-muted-foreground leading-relaxed">
        {t('This permanently erases your profile, holdings, watchlist, chat posts, saved scenarios and your reports. Billing records are kept for five years as the law requires. This cannot be undone.')}
      </p>
      <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={t('Type DELETE to confirm')}
             className="w-full h-9 px-3 text-sm rounded-md bg-card border border-border focus:outline-none focus:border-foreground" />
      {err && <p className="text-xs text-foreground">{err}</p>}
      <div className="flex gap-2">
        <button type="button" onClick={() => { setOpen(false); setTyped(''); setErr(null); }}
                className="h-8 px-3 rounded-full text-xs font-semibold border border-border">{t('Cancel')}</button>
        <button type="button" disabled={typed.trim() !== 'DELETE' || busy} onClick={go}
                className="h-8 px-3 rounded-full text-xs font-semibold bg-primary text-primary-foreground disabled:opacity-40">
          {busy ? t('Deleting…') : t('Delete permanently')}
        </button>
      </div>
    </div>
  );
}

function Row({ icon: Icon, label, onClick, muted = false }: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  muted?: boolean;
}) {
  const { t } = useI18n();
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/60 transition-colors"
    >
      <Icon size={18} strokeWidth={1.8} className={muted ? 'text-muted-foreground' : 'text-muted-foreground'} />
      <span className={`text-sm font-medium ${muted ? 'text-muted-foreground' : 'text-foreground'}`}>{t(label)}</span>
    </button>
  );
}

export function MenuPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const handleNav = useAppNav();
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);

  useEffect(() => {
    // One row per ticker: re-runs of the same name collapse to the latest.
    getHistory({ page: 1, page_size: 20 })
      .then((res) => {
        const seen = new Set<string>();
        setRecentRuns(res.items.filter((r) => !seen.has(r.ticker) && !!seen.add(r.ticker)).slice(0, 5));
      })
      .catch(() => {});
  }, []);

  // The two ReportPage entry points and the long-tail destinations live in
  // NAV_ITEMS; look them up by stable keys (action / path), not labels.
  const byAction = (a: 'new' | 'resume') => NAV_ITEMS.find((i) => i.action === a)!;
  const byPath = (p: string) => NAV_ITEMS.find((i) => i.path === p)!;

  const initial = (user?.name ?? user?.email ?? '?')[0]?.toUpperCase() ?? '?';

  return (
    <div className="min-h-full bg-background ui-flat">
      {/* pt-16 clears the floating avatar (mobile); desktop gets normal py. */}
      <div className="mx-auto w-full max-w-3xl px-4 md:px-8 pt-16 pb-10 md:pt-8 space-y-6">
        <button
          onClick={() => navigate(-1)}
          aria-label={t('Back')}
          className="w-11 h-11 rounded-full flex items-center justify-center border border-border/70 bg-card shadow-sm hover:bg-muted transition-colors"
        >
          <ChevronLeft size={18} className="text-foreground" />
        </button>

        {/* Identity header */}
        <div className="flex items-center gap-4">
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt="" className="w-12 h-12 rounded-full object-cover" />
          ) : (
            <div className="w-12 h-12 rounded-full bg-muted flex items-center justify-center text-lg font-bold text-foreground">
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
                <p className="text-xs font-semibold text-muted-foreground/60 mb-1">{t('Recent')}</p>
                <div className="space-y-0.5">
                  {recentRuns.map((run) => (
                    <button
                      key={run.run_id}
                      onClick={() => navigate(`/report/${run.run_id}`)}
                      className="w-full flex items-center gap-2 px-2 py-2 rounded-lg hover:bg-muted/60 transition-colors text-left"
                    >
                      <span className="font-mono text-xs font-bold text-foreground min-w-[48px]">{run.ticker}</span>
                      {run.final_action && (
                        <span className={`text-xs px-1.5 py-0.5 rounded font-bold leading-none ${actionTone(run.final_action)}`}>
                          {t(run.final_action.charAt(0) + run.final_action.slice(1).toLowerCase())}
                        </span>
                      )}
                      <span className="ml-auto text-xs text-muted-foreground/50 font-mono">
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
            {/* Watchlist and News are in NAV_ITEMS and therefore in the desktop
                sidebar, but this menu and FloatingNavBar each carry their own
                list, so neither appeared on mobile at all — Watchlist was
                reachable only by typing the URL. Looked up byPath so they stay
                tied to the same nav definitions the sidebar uses. */}
            <Row icon={BookMarked} label="Watchlist" onClick={() => handleNav(byPath('/watchlist'))} />
            <Row icon={Newspaper} label="News" onClick={() => handleNav(byPath('/news'))} />
            <Row icon={PieChart} label="Portfolio" onClick={() => handleNav(byPath('/portfolio'))} />
            <Row icon={MessageSquare} label="Discuss" onClick={() => handleNav(byPath('/discuss'))} />
            {user?.can_view_model_accuracy && (
              <Row
                icon={byPath('/model-accuracy').icon}
                label="Model Accuracy"
                onClick={() => handleNav(byPath('/model-accuracy'))}
              />
            )}
            <Row icon={Zap} label="Pricing" onClick={() => navigate('/pricing')} />
          </Card>
        </section>

        <section>
          <SectionLabel>Preferences</SectionLabel>
          <Card>
            <div className="px-4 py-3">
              <p className="text-xs font-semibold text-muted-foreground/60 mb-2">{t('Theme')}</p>
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
                    <span className="text-xs font-medium">{t(label)}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="px-4 py-3">
              <p className="text-xs font-semibold text-muted-foreground/60 mb-2">{t('Layout')}</p>
              <LayoutModeToggle />
            </div>
            <div className="px-4 py-3">
              <p className="text-xs font-semibold text-muted-foreground/60 mb-2">{t('Language')}</p>
              <LanguageToggle />
            </div>
            <Row icon={LogOut} label="Sign out" muted onClick={() => logout()} />
          </Card>
        </section>

        {/* Owner, 2026-11-28 (PDPA): legal documents and self-service account deletion. */}
        <section>
          <SectionLabel>Account</SectionLabel>
          <Card>
            <Row icon={FileText} label="Terms & Privacy" onClick={() => navigate('/legal/terms')} />
            <DeleteAccountRow onDeleted={() => logout()} />
          </Card>
        </section>

        {/* Fallback identity row for screen readers / empty states */}
        {!user && (
          <p className="text-xs text-muted-foreground flex items-center gap-2">
            <User size={14} /> {t('Not signed in.')}
          </p>
        )}
      </div>
    </div>
  );
}

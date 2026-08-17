/**
 * nav-config.ts
 * =============
 * Single source of truth for the primary navigation, shared by the mobile
 * menu page (MenuPage) and the desktop sidebar (DesktopSidebar) so the two
 * never drift apart.
 *
 * `useAppNav` encapsulates the two special-cased nav actions. These are keyed
 * off `NavItem.action` (NOT the display label) so the labels can be renamed
 * freely without breaking behavior:
 *   • action 'new'    → force-remounts ReportPage via a unique ?new=<ts>
 *                       query param (so clicking it while already on /report
 *                       still resets the page) + { fresh: true } router state.
 *   • action 'resume' → navigates to /report with { resume: true } so the
 *                       page restores the in-progress/last run instead of
 *                       starting fresh.
 *
 * "New Analysis" (action 'new') and "Current Analysis" (action 'resume') both
 * route to /report but mean different things; the distinct labels + `hint`
 * tooltips disambiguate them in the sidebar.
 */
import {
  Plus, BarChart2, Filter, BookMarked, Lightbulb, History, Wallet, MessageSquare,
  type LucideIcon,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';

export interface NavItem {
  label: string;
  icon: LucideIcon;
  path: string;
  /** Special ReportPage behavior, decoupled from the display label. */
  action?: 'new' | 'resume';
  /** Optional tooltip shown on hover (desktop sidebar). */
  hint?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { label: 'New Analysis',     icon: Plus,       path: '/report',         action: 'new',    hint: 'Start a fresh ticker analysis from a blank form' },
  { label: 'Current Analysis', icon: BarChart2,  path: '/report',         action: 'resume', hint: 'Resume your in-progress or most recent analysis' },
  { label: 'Screener',         icon: Filter,     path: '/screener'       },
  { label: 'Watchlist',        icon: BookMarked, path: '/watchlist'      },
  { label: 'Discuss',          icon: MessageSquare, path: '/discuss'     },
  { label: 'Robo Strategy',    icon: Wallet,     path: '/robo-strategy',  hint: 'Get a personalized portfolio recommendation' },
  { label: 'Research Ideas',   icon: Lightbulb,  path: '/research-ideas' },
  { label: 'History',          icon: History,    path: '/history'        },
];

/**
 * Returns a `handleNav(item)` callback wired to react-router. Pass an optional
 * `onNavigate` (e.g. to close a drawer/menu) that fires before the navigation.
 */
export function useAppNav(onNavigate?: () => void) {
  const navigate = useNavigate();
  return (item: NavItem) => {
    onNavigate?.();
    const { path, action } = item;
    if (action === 'new') {
      // Unique query param forces React Router to remount ReportPage even if
      // we're already on /report.
      navigate(`${path}?new=${Date.now()}`, { state: { fresh: true } });
    } else if (action === 'resume') {
      navigate(path, { state: { resume: true } });
    } else {
      navigate(path);
    }
  };
}

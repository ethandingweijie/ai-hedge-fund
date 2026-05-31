/**
 * AppShell.tsx
 * ============
 * Chooses the navigation shell based on the persisted layout mode:
 *   • 'desktop' → DesktopLayout  (persistent sidebar + wide content)
 *   • 'mobile'  → MobileLayout   (430px phone frame + hamburger drawer)
 *
 * The /login route is a standalone full-screen experience and renders WITHOUT
 * any shell in either mode (no nav chrome before the user is authenticated).
 */
import { useLocation } from 'react-router-dom';
import { useLayoutMode } from '@/contexts/layout-mode-context';
import { MobileLayout } from './mobile/MobileLayout';
import { DesktopLayout } from './desktop/DesktopLayout';

export function AppShell({ children }: { children: React.ReactNode }) {
  const { mode } = useLayoutMode();
  const location = useLocation();

  if (location.pathname === '/login') {
    return <>{children}</>;
  }

  return mode === 'desktop'
    ? <DesktopLayout>{children}</DesktopLayout>
    : <MobileLayout>{children}</MobileLayout>;
}

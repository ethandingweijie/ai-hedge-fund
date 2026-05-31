/**
 * DesktopLayout.tsx
 * =================
 * Desktop / iPad shell: a persistent left sidebar + a fluid, scrollable main
 * content area. Pages keep their own max-width (HK50/SW46/Complacency are
 * already max-w-7xl, Report max-w-6xl, Watchlist max-w-screen-xl, Screener
 * full-bleed), so dropping the 430px phone frame lets them use the width.
 *
 * Owns the sidebar collapse preference (persisted). Defaults to collapsed on
 * narrower "desktop" widths (e.g. iPad portrait) so content isn't cramped.
 */
import { useCallback, useState } from 'react';
import { DesktopSidebar } from './DesktopSidebar';

const STORAGE_KEY = 'sidebar-collapsed';

function initialCollapsed(): boolean {
  const stored = typeof window !== 'undefined' ? localStorage.getItem(STORAGE_KEY) : null;
  if (stored === 'true') return true;
  if (stored === 'false') return false;
  // No saved preference → collapse on narrower screens (e.g. iPad portrait).
  return typeof window !== 'undefined' ? window.innerWidth < 1024 : false;
}

export function DesktopLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState<boolean>(initialCollapsed);

  const toggleCollapse = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(STORAGE_KEY, String(next));
      return next;
    });
  }, []);

  return (
    // Safe-area insets: when launched as a standalone home-screen app on iPad,
    // iOS draws content edge-to-edge under the translucent status bar
    // (viewport-fit=cover). Padding the shell by env(safe-area-inset-*) keeps
    // the sidebar header + page content clear of the clock/battery and the
    // bottom home indicator. No-ops to 0 in a normal browser tab.
    <div
      className="flex h-screen overflow-hidden bg-background"
      style={{
        paddingTop: 'env(safe-area-inset-top)',
        paddingBottom: 'env(safe-area-inset-bottom)',
      }}
    >
      <DesktopSidebar collapsed={collapsed} onToggleCollapse={toggleCollapse} />
      <main className="flex-1 min-w-0 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}

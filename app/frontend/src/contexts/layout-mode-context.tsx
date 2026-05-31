/**
 * layout-mode-context.tsx
 * =======================
 * Drives whether the app renders the phone-frame MobileLayout or the
 * desktop/iPad shell (persistent sidebar + wide content).
 *
 * Behaviour locked with the user:
 *   • DEFAULT is auto-detected by viewport width on FIRST load
 *     (>= DESKTOP_MIN_WIDTH → desktop, else mobile).
 *   • An explicit user choice (via the sidebar toggle) is persisted to
 *     localStorage and ALWAYS wins thereafter — we never silently flip a
 *     mode the user picked, even if they resize the window.
 *   • iPad/tablets (~768-1024px) fall under "desktop" by virtue of the
 *     1024px cutoff being the *default* only; the user can still drop to
 *     mobile manually.
 */
import { createContext, useContext, useState, useCallback, useEffect } from 'react';

export type LayoutMode = 'mobile' | 'desktop';

const STORAGE_KEY = 'layout-mode';
/** Viewport width (px) at/above which we default new visitors to desktop. */
export const DESKTOP_MIN_WIDTH = 1024;

function detectDefault(): LayoutMode {
  if (typeof window === 'undefined') return 'mobile';
  return window.innerWidth >= DESKTOP_MIN_WIDTH ? 'desktop' : 'mobile';
}

function initialMode(): LayoutMode {
  const stored = (typeof window !== 'undefined'
    ? localStorage.getItem(STORAGE_KEY)
    : null) as LayoutMode | null;
  if (stored === 'mobile' || stored === 'desktop') return stored;
  return detectDefault();
}

const LayoutModeContext = createContext<{
  mode: LayoutMode;
  setMode: (m: LayoutMode) => void;
  toggle: () => void;
}>({
  mode: 'mobile',
  setMode: () => {},
  toggle: () => {},
});

export function LayoutModeProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<LayoutMode>(initialMode);

  // Expose the active mode to CSS via a data-attribute on <html>. This lets us
  // scope desktop-only styling (e.g. a larger root font on big screens) WITHOUT
  // a bare `@media (min-width: …)` query — which would also scale the 430px
  // mobile phone-frame preview that desktop users can render. CSS targets
  // `html[data-layout="desktop"]` so the phone frame is never affected.
  useEffect(() => {
    document.documentElement.dataset.layout = mode;
  }, [mode]);

  const setMode = useCallback((m: LayoutMode) => {
    localStorage.setItem(STORAGE_KEY, m);
    setModeState(m);
  }, []);

  const toggle = useCallback(() => {
    setModeState((prev) => {
      const next: LayoutMode = prev === 'mobile' ? 'desktop' : 'mobile';
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return (
    <LayoutModeContext.Provider value={{ mode, setMode, toggle }}>
      {children}
    </LayoutModeContext.Provider>
  );
}

export const useLayoutMode = () => useContext(LayoutModeContext);

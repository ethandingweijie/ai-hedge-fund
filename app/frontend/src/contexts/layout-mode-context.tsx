/**
 * layout-mode-context.tsx
 * =======================
 * Drives whether the app renders the phone-frame MobileLayout or the
 * desktop/iPad shell (persistent sidebar + wide content).
 *
 * Device-driven behaviour (locked 2026-08-16, after the "my laptop is stuck
 * in the truncated mobile view" complaint):
 *   • The mode is AUTO-DETECTED from the device on load — laptops and iPads
 *     get the desktop shell, phones get the mobile frame. Detection combines
 *     viewport width with a pointer/screen-size heuristic so a phone rotated
 *     to landscape (innerWidth 667–932) still counts as a phone while an iPad
 *     in portrait (768–1024) gets the desktop shell.
 *   • The app follows the device: resizing the window / rotating an iPad
 *     re-classifies and switches shells live.
 *   • An explicit choice via the layout toggle overrides auto-detection for
 *     the CURRENT SESSION ONLY — it is never persisted, so a stale stored
 *     preference can no longer pin the wrong shell on the next visit (that
 *     pinning is what made laptops show the phone frame).
 */
import { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';

export type LayoutMode = 'mobile' | 'desktop';

/** Viewport widths (px) at/below which the device is always a phone. */
export const MOBILE_MAX_WIDTH = 767;
/** Min screen dimension (CSS px) that marks a tablet-class device. */
const TABLET_MIN_SCREEN = 768;

/** Classify the current device: laptop / iPad → desktop, phone → mobile. */
function classifyDevice(): LayoutMode {
  if (typeof window === 'undefined') return 'mobile';
  if (window.innerWidth <= MOBILE_MAX_WIDTH) return 'mobile';
  // In the 768–932px band, innerWidth alone can't tell a landscape phone from
  // an iPad in portrait. But a phone's *smaller* screen dimension is always
  // under 768 CSS px while a tablet's never is — and laptops have a fine
  // pointer, so they fall straight through to desktop.
  const coarse = window.matchMedia?.('(pointer: coarse)')?.matches ?? false;
  if (coarse && Math.min(window.screen.width, window.screen.height) < TABLET_MIN_SCREEN) return 'mobile';
  return 'desktop';
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
  const [mode, setModeState] = useState<LayoutMode>(classifyDevice);
  // Set once the user explicitly picks a mode this session; auto-detection
  // then stands down until the next load.
  const manualOverride = useRef(false);

  // Expose the active mode to CSS via a data-attribute on <html>. This lets us
  // scope desktop-only styling (e.g. a larger root font on big screens) WITHOUT
  // a bare `@media (min-width: …)` query — which would also scale the 430px
  // mobile phone-frame preview that desktop users can render. CSS targets
  // `html[data-layout="desktop"]` so the phone frame is never affected.
  useEffect(() => {
    document.documentElement.dataset.layout = mode;
  }, [mode]);

  // Follow the device: re-classify on viewport changes (window resize, iPad
  // rotation, devtools docking) unless the user picked a mode this session.
  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | undefined;
    const onResize = () => {
      if (manualOverride.current) return;
      clearTimeout(t);
      t = setTimeout(() => setModeState(classifyDevice()), 150);
    };
    window.addEventListener('resize', onResize);
    return () => {
      clearTimeout(t);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  // Session-only overrides — deliberately NOT persisted (see header note).
  const setMode = useCallback((m: LayoutMode) => {
    manualOverride.current = true;
    setModeState(m);
  }, []);

  const toggle = useCallback(() => {
    manualOverride.current = true;
    setModeState(prev => (prev === 'mobile' ? 'desktop' : 'mobile'));
  }, []);

  return (
    <LayoutModeContext.Provider value={{ mode, setMode, toggle }}>
      {children}
    </LayoutModeContext.Provider>
  );
}

export const useLayoutMode = () => useContext(LayoutModeContext);

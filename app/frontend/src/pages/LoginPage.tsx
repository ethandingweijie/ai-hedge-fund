/**
 * LoginPage.tsx — Reimagined UI
 *
 * Minimal-fintech Linear/Stripe aesthetic. Zinc-neutral palette, 1px borders,
 * Brand accent is the cobalt --brand token. Wires real Google GSI + Apple
 * OAuth flows from auth-context into the new button shells.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';
import { EquitableMark } from '@/components/brand/EquitableMark';

declare global {
  interface Window {
    AppleID?: {
      auth: {
        init: (cfg: object) => void;
        signIn: () => Promise<{
          authorization: { id_token: string };
          user?: { name?: { firstName?: string; lastName?: string } };
        }>;
      };
    };
  }
}

function Divider({ className = '' }: { className?: string }) {
  return <div className={`h-px bg-border ${className}`} />;
}

function Check({ width = 12, height = 12 }: { width?: number; height?: number }) {
  return (
    <svg width={width} height={height} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export function LoginPage() {
  const { loginWithGoogle, loginWithApple, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string })?.from ?? '/report';

  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [googleBtnWidth, setGoogleBtnWidth] = useState<number | null>(null);
  const appleScriptRef = useRef(false);
  const googleBtnRef = useRef<HTMLDivElement>(null);
  const heroVideoRef = useRef<HTMLVideoElement>(null);
  const heroVideoDarkRef = useRef<HTMLVideoElement>(null);

  // Already logged in → redirect
  useEffect(() => {
    if (user) navigate(from, { replace: true });
  }, [user, navigate, from]);

  // Hero videos — slow-motion playback. Both the light-mode and dark-mode
  // videos are mounted concurrently; Tailwind `dark:hidden` / `hidden dark:block`
  // toggles visibility, but either can be driven regardless of theme. iOS
  // sometimes blocks autoplay until a user gesture despite `muted` — we retry
  // silently; the static first frame is the fallback.
  useEffect(() => {
    const boot = (v: HTMLVideoElement | null) => {
      if (!v) return;
      v.playbackRate = 0.5; // slow motion
      const tryPlay = () => v.play().catch(() => { /* blocked — poster/first frame is the fallback */ });
      if (v.readyState >= 2) tryPlay(); else v.addEventListener('loadeddata', tryPlay, { once: true });
    };
    boot(heroVideoRef.current);
    boot(heroVideoDarkRef.current);
  }, []);

  // Apple Sign In SDK
  useEffect(() => {
    if (appleScriptRef.current) return;
    appleScriptRef.current = true;
    const script = document.createElement('script');
    script.src = 'https://appleid.cdn-apple.com/appleauth/static/jsapi/appleid/1/en_US/appleid.auth.js';
    script.async = true;
    script.onload = () => {
      const clientId = import.meta.env.VITE_APPLE_CLIENT_ID;
      if (clientId && window.AppleID) {
        window.AppleID.auth.init({
          clientId,
          scope: 'name email',
          redirectURI: window.location.origin,
          usePopup: true,
        });
      }
    };
    document.head.appendChild(script);
  }, []);

  // Measure the auth-buttons column so the Google GSI button (which only
  // accepts a fixed pixel width, no 100%/auto) can be rendered at exactly
  // the same width as the Apple button's `w-full`. Re-measures on resize
  // (e.g. orientation change) since GSI can't resize itself after render.
  // Ignores sub-pixel jitter so it doesn't re-render on every layout tick.
  useEffect(() => {
    const el = googleBtnRef.current?.parentElement;
    if (!el) return;
    const update = () => {
      const next = Math.round(el.clientWidth);
      setGoogleBtnWidth((prev) => (prev != null && Math.abs(prev - next) < 2 ? prev : next));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Google GSI — renders an invisible button we trigger programmatically.
  // `initialize()` only needs to run once; only `renderButton()` re-runs
  // when the measured width changes (e.g. orientation change).
  const googleInitializedRef = useRef(false);
  useEffect(() => {
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;
    if (!clientId || googleBtnWidth == null) return;
    const render = () => {
      const w = window as any;
      if (!w.google?.accounts?.id) return;
      if (!googleInitializedRef.current) {
        w.google.accounts.id.initialize({
          client_id: clientId,
          callback: (resp: { credential: string }) => handleGoogleCredential(resp.credential),
        });
        googleInitializedRef.current = true;
      }
      if (googleBtnRef.current) {
        // GSI clamps width to [200, 400] — clamp locally too so a very
        // narrow or wide container doesn't silently mismatch the Apple button.
        w.google.accounts.id.renderButton(googleBtnRef.current, {
          theme: 'outline',
          size: 'large',
          width: Math.min(400, Math.max(200, googleBtnWidth)),
          text: 'continue_with',
          shape: 'rectangular',
          logo_alignment: 'center',
        });
      }
    };
    const existing = document.getElementById('google-gsi-script');
    if (existing) {
      render();
      return;
    }
    const script = document.createElement('script');
    script.id = 'google-gsi-script';
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.onload = render;
    document.head.appendChild(script);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [googleBtnWidth]);

  function handleGoogleCredential(credential: string) {
    setError(null);
    setLoading('google');
    loginWithGoogle(credential)
      .then(() => navigate(from, { replace: true }))
      .catch((e) => { setError(e.message); setLoading(null); });
  }

  async function handleAppleSignIn() {
    if (!window.AppleID) {
      setError('Apple Sign In is not available. Ensure VITE_APPLE_CLIENT_ID is set.');
      return;
    }
    setError(null);
    setLoading('apple');
    try {
      const res = await window.AppleID.auth.signIn();
      await loginWithApple(
        res.authorization.id_token,
        res.user?.name?.firstName,
        res.user?.name?.lastName,
      );
      navigate(from, { replace: true });
    } catch (e: any) {
      if (e?.error !== 'popup_closed_by_user') {
        setError('Apple sign-in failed. Please try again.');
      }
      setLoading(null);
    }
  }

  return (
    <div className="min-h-screen w-full flex flex-col bg-background relative overflow-hidden">
      {/* ── Hero video background — LIGHT MODE ────────────────────────────────
         Slow-motion looped footage recoloured to Equitable green hue. Hidden
         in dark mode. Muted + playsInline so it autoplays on mobile. */}
      <div className="absolute inset-0 z-0 pointer-events-none dark:hidden">
        <video
          ref={heroVideoRef}
          className="absolute inset-0 w-full h-full object-cover"
          style={{
            // Fully desaturated: the footage reads as grey smoke over the
            // white page rather than carrying any brand tint.
            filter: 'grayscale(1) brightness(1.05) contrast(0.95)',
            opacity: 0.55,
          }}
          src="/landing-hero.mp4"
          autoPlay
          muted
          loop
          playsInline
          preload="auto"
          aria-hidden="true"
        />
        {/* Neutral white wash so the sign-in content stays legible */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'linear-gradient(180deg, rgba(255,255,255,0.15) 0%, rgba(255,255,255,0.55) 55%, rgba(255,255,255,0.92) 100%)',
          }}
        />
        {/* Soft vignette so content remains legible over moving footage */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(120% 80% at 50% 40%, transparent 35%, rgba(255,255,255,0.6) 100%)',
          }}
        />
      </div>

      {/* ── Hero video background — DARK MODE ────────────────────────────────
         Descending green-hue footage (already green-tinted, so no hue-rotate).
         Shown only in dark mode. */}
      <div className="absolute inset-0 z-0 pointer-events-none hidden dark:block">
        <video
          ref={heroVideoDarkRef}
          className="absolute inset-0 w-full h-full object-cover"
          style={{
            // Desaturated and lifted so the smoke reads light grey against
            // the pure-black canvas instead of sinking into it.
            filter: 'grayscale(1) brightness(1.15) contrast(1.05)',
            opacity: 0.55,
          }}
          src="/landing-hero-dark.mp4"
          autoPlay
          muted
          loop
          playsInline
          preload="auto"
          aria-hidden="true"
        />
        {/* Dark wash: black fades in toward the bottom so the sign-in card sits on a solid surface */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'linear-gradient(180deg, rgba(0,0,0,0.35) 0%, rgba(0,0,0,0.55) 55%, rgba(0,0,0,0.85) 100%)',
          }}
        />
        {/* Radial vignette — dark edges, lighter centre */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(120% 80% at 50% 40%, transparent 35%, rgba(0,0,0,0.7) 100%)',
          }}
        />
      </div>

      <div className="relative z-10 flex-1 flex flex-col justify-center px-7 pt-12 max-w-sm mx-auto w-full">

        <div className="relative w-full">
          {/* Logo */}
          <div className="flex items-center gap-2.5 mb-10">
            <EquitableMark size={28} />
            <span className="text-[17px] font-semibold tracking-tight text-foreground">
              Equitable
            </span>
          </div>

          {/* Heading */}
          <h1 className="text-[28px] leading-[1.1] font-semibold tracking-tight text-foreground">
            Sign in
          </h1>
          <p className="text-[14px] text-muted-foreground mt-2">
            Investment research, on every market that matters.
          </p>

          {/* Error */}
          {error && (
            <div className="mt-6 text-[13px] rounded-lg border border-[var(--hairline)] bg-surface-2 text-content-high px-3.5 py-2.5">
              {error}
            </div>
          )}

          {/* Auth buttons */}
          <div className="mt-8 space-y-2.5">
            {/* Google — GSI renders its own button into the ref */}
            {import.meta.env.VITE_GOOGLE_CLIENT_ID ? (
              <div
                ref={googleBtnRef}
                // GSI's own "rectangular" shape has a small ~4px corner radius
                // it doesn't expose a way to configure — overflow-hidden here
                // clips its rendered iframe to our rounded-lg token instead,
                // so it reads as rounded as the Apple button beside it.
                className={`w-full flex justify-center overflow-hidden rounded-lg ${loading === 'google' ? 'opacity-60 pointer-events-none' : ''}`}
                style={{ minHeight: 40 }}
              />
            ) : (
              <div className="w-full h-10 rounded-lg border border-border bg-card text-[14px] font-medium text-muted-foreground/70 flex items-center justify-center gap-2.5 select-none">
                Google (configure VITE_GOOGLE_CLIENT_ID)
              </div>
            )}

            {/* Apple — height matches Google GSI's fixed "large" size (40px);
                GSI has no way to render taller, so this side conforms to it. */}
            <button
              type="button"
              onClick={handleAppleSignIn}
              disabled={!!loading}
              // min-h-10 (not just h-10) is required: the global mobile
              // tap-target rule (index.css, `button, a { min-height: 44px }`
              // under 768px) otherwise wins on phones and stretches this
              // button back past Google's hard-capped 40px GSI height.
              className="w-full h-10 min-h-10 rounded-lg bg-foreground active:bg-foreground/85 text-[14px] font-medium text-background flex items-center justify-center gap-2.5 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {loading === 'apple' ? (
                <div className="w-4 h-4 border-2 border-background/40 border-t-background rounded-full animate-spin" />
              ) : (
                <svg width="14" height="17" viewBox="0 0 17 20" fill="currentColor">
                  <path d="M13.87 10.56c-.02-2.17 1.77-3.21 1.85-3.27-1.01-1.48-2.58-1.68-3.14-1.7-1.33-.14-2.6.79-3.28.79-.68 0-1.72-.77-2.83-.75-1.45.02-2.79.85-3.54 2.15C1.1 10.4 2.13 14.7 3.9 17.12c.88 1.27 1.93 2.69 3.3 2.64 1.33-.05 1.83-.86 3.43-.86 1.6 0 2.05.86 3.44.84 1.43-.02 2.33-1.29 3.2-2.57.99-1.47 1.4-2.88 1.43-2.96-.03-.01-2.76-1.06-2.79-4.19l-.04-.46zM11.4 3.6C12.1 2.74 12.57 1.55 12.44.34c-1.04.04-2.3.7-3.04 1.55-.67.77-1.25 2-1.1 3.17 1.16.09 2.34-.59 3.1-1.46z" />
                </svg>
              )}
              {loading === 'apple' ? 'Signing in…' : 'Continue with Apple'}
            </button>
          </div>

          {/* Market chip divider */}
          <div className="mt-8 flex items-center gap-3">
            <Divider className="flex-1" />
            <span className="text-[11px] text-muted-foreground/70 uppercase tracking-[0.1em]">
              US · HK · SGX
            </span>
            <Divider className="flex-1" />
          </div>

          <p className="text-[11px] text-muted-foreground/70 text-center mt-6 leading-relaxed">
            By signing in you agree to the Terms &amp; Privacy. Your searches are private to your account.
          </p>
        </div>
      </div>

      {/* Footer */}
      <div className="relative z-10 h-10 border-t border-border/60 bg-background/70 dark:bg-transparent backdrop-blur-sm flex items-center justify-center text-[11px] text-muted-foreground/70">
        <span className="inline-flex items-center gap-1.5">
          <Check width={12} height={12} /> Secure · Private · v1.7.1
        </span>
      </div>
    </div>
  );
}

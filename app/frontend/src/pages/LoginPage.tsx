/**
 * LoginPage.tsx — Reimagined UI
 *
 * Minimal-fintech Linear/Stripe aesthetic. Zinc-neutral palette, 1px borders,
 * Equitable green (#2e7d32) reserved for logo. Wires real Google GSI + Apple
 * OAuth flows from auth-context into the new button shells.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';

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

const BRAND = '#2e7d32';

function Leaf({ size = 28 }: { size?: number }) {
  return (
    <div
      className="rounded-[6px] flex items-center justify-center text-white font-extrabold"
      style={{
        backgroundColor: BRAND,
        width: size,
        height: size,
        fontSize: size * 0.65,
        lineHeight: 1,
        letterSpacing: '-0.04em',
      }}
    >
      e
    </div>
  );
}

function Divider({ className = '' }: { className?: string }) {
  return <div className={`h-px bg-zinc-200 dark:bg-zinc-800 ${className}`} />;
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

  // Google GSI — renders an invisible button we trigger programmatically
  useEffect(() => {
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;
    if (!clientId) return;
    const existing = document.getElementById('google-gsi-script');
    if (existing) {
      initGoogleButton(clientId);
      return;
    }
    const script = document.createElement('script');
    script.id = 'google-gsi-script';
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.onload = () => initGoogleButton(clientId);
    document.head.appendChild(script);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function initGoogleButton(clientId: string) {
    const w = window as any;
    if (!w.google?.accounts?.id) return;
    w.google.accounts.id.initialize({
      client_id: clientId,
      callback: (resp: { credential: string }) => handleGoogleCredential(resp.credential),
    });
    if (googleBtnRef.current) {
      w.google.accounts.id.renderButton(googleBtnRef.current, {
        theme: 'outline',
        size: 'large',
        width: 340,
        text: 'continue_with',
        shape: 'rectangular',
        logo_alignment: 'center',
      });
    }
  }

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
    <div className="min-h-screen w-full flex flex-col bg-white dark:bg-zinc-900 relative overflow-hidden">
      {/* ── Hero video background — LIGHT MODE ────────────────────────────────
         Slow-motion looped footage recoloured to Equitable green hue. Hidden
         in dark mode. Muted + playsInline so it autoplays on mobile. */}
      <div className="absolute inset-0 z-0 pointer-events-none dark:hidden">
        <video
          ref={heroVideoRef}
          className="absolute inset-0 w-full h-full object-cover"
          style={{
            // Shift hues toward green, soften saturation so the tint reads as
            // a brand wash rather than the original footage colour.
            filter: 'hue-rotate(80deg) saturate(0.9) brightness(1.05) contrast(0.95)',
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
        {/* Equitable green colour wash on top of the video for brand consistency */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'linear-gradient(180deg, rgba(46,125,50,0.22) 0%, rgba(255,255,255,0.55) 55%, rgba(255,255,255,0.92) 100%)',
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
            // Footage is already green-themed — just dim + soften saturation
            // so it sits behind the zinc-900 surface as ambient motion.
            filter: 'saturate(1.05) brightness(0.85) contrast(1.0)',
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
        {/* Dark wash: zinc-900 fades in toward the bottom so the sign-in card sits on a solid surface */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'linear-gradient(180deg, rgba(24,24,27,0.35) 0%, rgba(24,24,27,0.55) 55%, rgba(24,24,27,0.85) 100%)',
          }}
        />
        {/* Radial vignette — dark edges, lighter centre */}
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(120% 80% at 50% 40%, transparent 35%, rgba(24,24,27,0.7) 100%)',
          }}
        />
      </div>

      <div className="relative z-10 flex-1 flex flex-col justify-center px-7 pt-12 max-w-sm mx-auto w-full">

        <div className="relative w-full">
          {/* Logo */}
          <div className="flex items-center gap-2.5 mb-10">
            <Leaf size={28} />
            <span className="text-[17px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Equitable
            </span>
          </div>

          {/* Heading */}
          <h1 className="text-[28px] leading-[1.1] font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Sign in
          </h1>
          <p className="text-[14px] text-zinc-500 dark:text-zinc-400 mt-2">
            Investment research, on every market that matters.
          </p>

          {/* Error */}
          {error && (
            <div className="mt-6 text-[13px] rounded-lg border border-red-200 bg-red-50 text-red-700 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300 px-3.5 py-2.5">
              {error}
            </div>
          )}

          {/* Auth buttons */}
          <div className="mt-8 space-y-2.5">
            {/* Google — GSI renders its own button into the ref */}
            {import.meta.env.VITE_GOOGLE_CLIENT_ID ? (
              <div
                ref={googleBtnRef}
                className={`w-full flex justify-center ${loading === 'google' ? 'opacity-60 pointer-events-none' : ''}`}
                style={{ minHeight: 48 }}
              />
            ) : (
              <div className="w-full h-12 rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 text-[14px] font-medium text-zinc-400 flex items-center justify-center gap-2.5 select-none">
                Google (configure VITE_GOOGLE_CLIENT_ID)
              </div>
            )}

            {/* Apple */}
            <button
              type="button"
              onClick={handleAppleSignIn}
              disabled={!!loading}
              className="w-full h-12 rounded-lg bg-zinc-900 dark:bg-white active:bg-zinc-800 dark:active:bg-zinc-200 text-[14px] font-medium text-white dark:text-zinc-900 flex items-center justify-center gap-2.5 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {loading === 'apple' ? (
                <div className="w-4 h-4 border-2 border-white/40 border-t-white dark:border-zinc-400 dark:border-t-zinc-900 rounded-full animate-spin" />
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
            <span className="text-[11px] text-zinc-400 dark:text-zinc-500 uppercase tracking-[0.1em]">
              US · HK · SGX
            </span>
            <Divider className="flex-1" />
          </div>

          <p className="text-[11px] text-zinc-400 dark:text-zinc-500 text-center mt-6 leading-relaxed">
            By signing in you agree to the Terms &amp; Privacy. Your searches are private to your account.
          </p>
        </div>
      </div>

      {/* Footer */}
      <div className="relative z-10 h-10 border-t border-zinc-100 dark:border-zinc-800 bg-white/70 dark:bg-transparent backdrop-blur-sm flex items-center justify-center text-[11px] text-zinc-400 dark:text-zinc-500">
        <span className="inline-flex items-center gap-1.5">
          <Check width={12} height={12} /> Secure · Private · v1.7.1
        </span>
      </div>
    </div>
  );
}

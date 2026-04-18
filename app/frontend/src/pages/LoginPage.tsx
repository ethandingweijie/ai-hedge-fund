/**
 * LoginPage.tsx
 * Full-screen login with Google and Apple sign-in.
 * Shares the same green leaf wallpaper as the home page.
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/contexts/auth-context';

declare global {
  interface Window {
    AppleID?: {
      auth: {
        init: (cfg: object) => void;
        signIn: () => Promise<{ authorization: { id_token: string }; user?: { name?: { firstName?: string; lastName?: string } } }>;
      };
    };
  }
}

export function LoginPage() {
  const { loginWithGoogle, loginWithApple, user } = useAuth();
  const navigate = useNavigate();
  const location  = useLocation();
  const from = (location.state as { from?: string })?.from ?? '/report';

  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null); // 'google' | 'apple'
  const appleScriptRef = useRef(false);

  // Already logged in → redirect
  useEffect(() => {
    if (user) navigate(from, { replace: true });
  }, [user, navigate, from]);

  // Load Apple Sign In JS SDK once
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

  // ── Google (GSI script renders the button, gives us id_token via callback) ──
  function handleGoogleCredential(credential: string) {
    setError(null);
    setLoading('google');
    loginWithGoogle(credential)
      .then(() => navigate(from, { replace: true }))
      .catch(e => { setError(e.message); setLoading(null); });
  }

  // Render Google One Tap button via GSI script (gives us id_token directly)
  const googleBtnRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;
    if (!clientId) return;

    // Load Google Identity Services script
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
        width: 320,
        text: 'signin_with',
        shape: 'pill',
      });
    }
  }

  // ── Apple ───────────────────────────────────────────────────────────────────
  async function handleAppleSignIn() {
    if (!window.AppleID) {
      setError('Apple Sign In is not available. Make sure VITE_APPLE_CLIENT_ID is set.');
      return;
    }
    setError(null);
    setLoading('apple');
    try {
      const res = await window.AppleID.auth.signIn();
      const idToken = res.authorization.id_token;
      const firstName = res.user?.name?.firstName;
      const lastName  = res.user?.name?.lastName;
      await loginWithApple(idToken, firstName, lastName);
      navigate(from, { replace: true });
    } catch (e: any) {
      if (e?.error !== 'popup_closed_by_user') {
        setError('Apple sign-in failed. Please try again.');
      }
      setLoading(null);
    }
  }

  return (
    <div
      className="min-h-screen flex flex-col items-center justify-center px-4"
      style={{
        backgroundImage: 'url(/bg-wallpaper.jpg)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
      }}
    >
      {/* Dark overlay */}
      <div className="absolute inset-0 bg-black/45 pointer-events-none" />

      {/* Card */}
      <div className="relative z-10 w-full max-w-sm bg-white/95 backdrop-blur-md rounded-3xl shadow-2xl px-8 py-10 flex flex-col items-center gap-6">

        {/* Logo / heading */}
        <div className="text-center">
          <img src="/icon-192x192.png" alt="Equitable" className="w-16 h-16 mx-auto mb-2" />
          <h1 className="text-2xl font-bold text-gray-900"
              style={{ fontFamily: "'Segoe UI', 'Google Sans', Arial, sans-serif" }}>
            Welcome to Equitable!
          </h1>
          <p className="text-sm text-gray-500 mt-1">Sign in to start your investment research</p>
        </div>

        {/* Error */}
        {error && (
          <div className="w-full bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl px-4 py-3 text-center">
            {error}
          </div>
        )}

        {/* Google button — rendered by GSI SDK into this div */}
        <div className="w-full flex flex-col items-center gap-3">
          {import.meta.env.VITE_GOOGLE_CLIENT_ID ? (
            <div ref={googleBtnRef} className={loading === 'google' ? 'opacity-60 pointer-events-none' : ''} />
          ) : (
            <div className="w-full flex items-center justify-center gap-3 h-12 rounded-full border border-gray-300 bg-white text-sm font-medium text-gray-400 cursor-not-allowed select-none">
              <GoogleIcon />
              Google (add VITE_GOOGLE_CLIENT_ID)
            </div>
          )}

          {/* Apple button */}
          <button
            type="button"
            onClick={handleAppleSignIn}
            disabled={!!loading}
            className="w-full flex items-center justify-center gap-3 h-12 rounded-full bg-black text-white text-sm font-medium hover:bg-gray-900 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            style={{ minWidth: 280 }}
          >
            {loading === 'apple' ? (
              <div className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
            ) : (
              <AppleIcon />
            )}
            {loading === 'apple' ? 'Signing in…' : 'Sign in with Apple'}
          </button>
        </div>

        {/* Divider note */}
        <p className="text-[11px] text-gray-400 text-center leading-relaxed">
          By signing in you agree to our terms. Your searches are stored in a shared database and visible only to you when logged in.
        </p>
      </div>
    </div>
  );
}

// ── Icons ──────────────────────────────────────────────────────────────────────

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
      <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
      <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
      <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
    </svg>
  );
}

function AppleIcon() {
  return (
    <svg width="17" height="20" viewBox="0 0 17 20" fill="white" xmlns="http://www.w3.org/2000/svg">
      <path d="M13.87 10.56c-.02-2.17 1.77-3.21 1.85-3.27-1.01-1.48-2.58-1.68-3.14-1.7-1.33-.14-2.6.79-3.28.79-.68 0-1.72-.77-2.83-.75-1.45.02-2.79.85-3.54 2.15C1.1 10.4 2.13 14.7 3.9 17.12c.88 1.27 1.93 2.69 3.3 2.64 1.33-.05 1.83-.86 3.43-.86 1.6 0 2.05.86 3.44.84 1.43-.02 2.33-1.29 3.2-2.57.99-1.47 1.4-2.88 1.43-2.96-.03-.01-2.76-1.06-2.79-4.19l-.04-.46zM11.4 3.6C12.1 2.74 12.57 1.55 12.44.34c-1.04.04-2.3.7-3.04 1.55-.67.77-1.25 2-1.1 3.17 1.16.09 2.34-.59 3.1-1.46z"/>
    </svg>
  );
}

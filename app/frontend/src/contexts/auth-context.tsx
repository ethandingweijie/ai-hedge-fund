/**
 * auth-context.tsx
 * Global authentication state.
 * Uses SecureStorage (iOS Keychain via Capacitor, localStorage on web).
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { API_BASE_URL } from '@/config';
import { SecureStorage } from '@/lib/secure-storage';

const STORAGE_KEY = 'hedge_fund_token';

// In-memory cache so synchronous callers (API headers) can access the token
let _cachedToken: string | null = null;

export interface AuthUser {
  id: number;
  email: string;
  name: string | null;
  avatar_url: string | null;
  provider: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  loginWithGoogle: (idToken: string) => Promise<void>;
  loginWithApple: (idToken: string, firstName?: string, lastName?: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser]     = useState<AuthUser | null>(null);
  const [token, setToken]   = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Re-hydrate from storage on mount
  useEffect(() => {
    SecureStorage.get(STORAGE_KEY).then(stored => {
      if (stored) {
        _cachedToken = stored;
        fetchMe(stored)
          .then(u => { setUser(u); setToken(stored); })
          .catch(() => { SecureStorage.remove(STORAGE_KEY); _cachedToken = null; })
          .finally(() => setLoading(false));
      } else {
        setLoading(false);
      }
    });
  }, []);

  async function fetchMe(jwt: string): Promise<AuthUser> {
    const res = await fetch(`${API_BASE_URL}/auth/me`, {
      headers: { Authorization: `Bearer ${jwt}` },
    });
    if (!res.ok) throw new Error('Token invalid');
    return res.json();
  }

  async function _login(endpoint: string, body: object) {
    const res = await fetch(`${API_BASE_URL}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail ?? 'Login failed');
    }
    const data = await res.json();
    await SecureStorage.set(STORAGE_KEY, data.access_token);
    _cachedToken = data.access_token;
    setToken(data.access_token);
    setUser(data.user as AuthUser);
  }

  async function loginWithGoogle(idToken: string) {
    await _login('/auth/google', { id_token: idToken });
  }

  async function loginWithApple(idToken: string, firstName?: string, lastName?: string) {
    await _login('/auth/apple', { id_token: idToken, first_name: firstName, last_name: lastName });
  }

  function logout() {
    SecureStorage.remove(STORAGE_KEY);
    _cachedToken = null;
    setUser(null);
    setToken(null);
  }

  return (
    <AuthContext.Provider value={{ user, token, loading, loginWithGoogle, loginWithApple, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

/** Returns the cached JWT for API calls outside React (synchronous). */
export function getStoredToken(): string | null {
  return _cachedToken;
}

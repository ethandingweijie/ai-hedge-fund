/**
 * auth-context.tsx
 * Global authentication state.
 * Stores the JWT in localStorage and exposes login/logout helpers.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

const API_BASE = 'http://localhost:8000';
const STORAGE_KEY = 'hedge_fund_token';

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

  // Re-hydrate from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      fetchMe(stored)
        .then(u => { setUser(u); setToken(stored); })
        .catch(() => localStorage.removeItem(STORAGE_KEY))
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  async function fetchMe(jwt: string): Promise<AuthUser> {
    const res = await fetch(`${API_BASE}/auth/me`, {
      headers: { Authorization: `Bearer ${jwt}` },
    });
    if (!res.ok) throw new Error('Token invalid');
    return res.json();
  }

  async function _login(endpoint: string, body: object) {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail ?? 'Login failed');
    }
    const data = await res.json();
    localStorage.setItem(STORAGE_KEY, data.access_token);
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
    localStorage.removeItem(STORAGE_KEY);
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

/** Returns just the stored JWT (for API calls outside React). */
export function getStoredToken(): string | null {
  return localStorage.getItem(STORAGE_KEY);
}

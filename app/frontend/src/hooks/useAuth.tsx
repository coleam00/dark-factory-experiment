/**
 * useAuth — session state hook backed by /api/auth/me, shared via AuthContext.
 *
 * On mount, `AuthProvider` calls `me()` once to hydrate the user AND their
 * daily rate-limit counter (MISSION §10 invariant #1 + issue #52).
 * Login/signup set the cookie, then `refresh()` re-hits `/me` so the counter
 * lands with the authoritative value from Postgres.
 *
 * `refresh()` is exposed so the chat area can call it after each successful
 * send — the counter needs to decrement by one per message. Because the
 * state lives in context, Sidebar and ChatArea see the same snapshot.
 */

import { ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import {
  AuthError,
  AuthMeResponse,
  login as apiLogin,
  logout as apiLogout,
  me,
  signup as apiSignup,
} from '../lib/authApi';

export type AuthStatus = 'loading' | 'authed' | 'anon';

export interface UseAuthResult {
  status: AuthStatus;
  user: AuthMeResponse | null;
  error: string | null;
  signup: (email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<UseAuthResult | null>(null);

function useAuthState(): UseAuthResult {
  const [user, setUser] = useState<AuthMeResponse | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const u = await me();
      setUser(u);
      setStatus('authed');
    } catch (e) {
      setUser(null);
      setStatus('anon');
      if (e instanceof AuthError && e.status !== 401) {
        setError(e.message);
      }
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const doLogin = useCallback(
    async (email: string, password: string) => {
      setError(null);
      try {
        await apiLogin(email, password);
        await refresh();
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Login failed';
        setError(msg);
        throw e;
      }
    },
    [refresh],
  );

  const doSignup = useCallback(
    async (email: string, password: string) => {
      setError(null);
      try {
        await apiSignup(email, password);
        await refresh();
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Signup failed';
        setError(msg);
        throw e;
      }
    },
    [refresh],
  );

  const doLogout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
      setStatus('anon');
    }
  }, []);

  return useMemo(
    () => ({
      status,
      user,
      error,
      signup: doSignup,
      login: doLogin,
      logout: doLogout,
      refresh,
    }),
    [status, user, error, doSignup, doLogin, doLogout, refresh],
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const value = useAuthState();
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): UseAuthResult {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return ctx;
}

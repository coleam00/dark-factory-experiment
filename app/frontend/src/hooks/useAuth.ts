/**
 * useAuth — session state hook backed by /api/auth/me.
 *
 * On mount, calls `me()` once to hydrate the user. Components consume the
 * returned shape to gate UI and show the current email. Login/signup/logout
 * helpers update local state after each successful call.
 *
 * No context provider — `me()` is cheap and this hook is only used in the
 * root-level auth guard + header. If that changes, wrap in Context.
 */

import { useCallback, useEffect, useState } from 'react';
import { AuthError, AuthUser, login as apiLogin, logout as apiLogout, me, signup as apiSignup } from '../lib/authApi';

export type AuthStatus = 'loading' | 'authed' | 'anon';

export interface UseAuthResult {
  status: AuthStatus;
  user: AuthUser | null;
  error: string | null;
  signup: (email: string, password: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

export function useAuth(): UseAuthResult {
  const [user, setUser] = useState<AuthUser | null>(null);
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

  const doLogin = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      const u = await apiLogin(email, password);
      setUser(u);
      setStatus('authed');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Login failed';
      setError(msg);
      throw e;
    }
  }, []);

  const doSignup = useCallback(async (email: string, password: string) => {
    setError(null);
    try {
      const u = await apiSignup(email, password);
      setUser(u);
      setStatus('authed');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Signup failed';
      setError(msg);
      throw e;
    }
  }, []);

  const doLogout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
      setStatus('anon');
    }
  }, []);

  return {
    status,
    user,
    error,
    signup: doSignup,
    login: doLogin,
    logout: doLogout,
    refresh,
  };
}

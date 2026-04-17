/**
 * Auth API client — signup, login, logout, me.
 *
 * All calls send `credentials: 'include'` so the `session` httpOnly cookie
 * round-trips on both the signup/login response (Set-Cookie) and subsequent
 * requests (Cookie header).
 */

const BASE = '/api/auth';

export interface AuthUser {
  id: string;
  email: string;
}

/**
 * Extended /me response — includes the sliding-window rate-limit counter so
 * the frontend can render the daily quota without a second request.
 *
 * - `messages_used_today`: rows in `user_messages` for this user within the
 *   last 24h (sliding window, not calendar day).
 * - `messages_remaining_today`: DAILY_MESSAGE_CAP - used, clamped to ≥0.
 * - `rate_window_resets_at`: ISO string = oldest_in_window + 24h, or null
 *   when the user has zero messages in the window (nothing to reset).
 */
export interface AuthMeResponse extends AuthUser {
  messages_used_today: number;
  messages_remaining_today: number;
  rate_window_resets_at: string | null;
}

export class AuthError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function authRequest<T>(path: string, init: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // fall through with statusText
    }
    throw new AuthError(res.status, detail);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

export const signup = (email: string, password: string) =>
  authRequest<AuthUser>('/signup', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });

export const login = (email: string, password: string) =>
  authRequest<AuthUser>('/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });

export const logout = () => authRequest<void>('/logout', { method: 'POST' });

export const me = () => authRequest<AuthMeResponse>('/me', { method: 'GET' });

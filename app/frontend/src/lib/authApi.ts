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
  is_admin: boolean;
  messages_used_today: number;
  messages_remaining_today: number;
  rate_window_resets_at: string | null;
}

/**
 * Scope of a signup 429. `"ip"` = this visitor just signed up; `"global"` =
 * the whole service is throttling. The frontend uses this to pick the copy.
 */
export type SignupRateLimitScope = 'ip' | 'global';

export class AuthError extends Error {
  status: number;
  /** Present only when the backend returned a structured 429 for signup. */
  rateLimitScope?: SignupRateLimitScope;
  constructor(status: number, message: string, rateLimitScope?: SignupRateLimitScope) {
    super(message);
    this.status = status;
    this.rateLimitScope = rateLimitScope;
  }
}

/**
 * Normalize FastAPI's polymorphic `detail` field into a human-readable string.
 *
 * Handlers return `{"detail": "..."}` for most errors, but the default
 * validation handler returns `{"detail": [{loc, msg, type}, ...]}`. Rendering
 * the array via JS string coercion produces "[object Object]" in the UI.
 */
function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (d && typeof d === 'object' && 'msg' in d && typeof (d as { msg: unknown }).msg === 'string') {
          return (d as { msg: string }).msg;
        }
        return null;
      })
      .filter((p): p is string => p !== null);
    return parts.length > 0 ? parts.join(', ') : null;
  }
  return null;
}

async function authRequest<T>(path: string, init: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    let scope: SignupRateLimitScope | undefined;
    try {
      const body = await res.json();
      if (body?.error === 'signup_rate_limited' && typeof body?.message === 'string') {
        detail = body.message;
        if (body.scope === 'ip' || body.scope === 'global') scope = body.scope;
      } else {
        const formatted = formatDetail(body?.detail);
        if (formatted) detail = formatted;
      }
    } catch {
      // fall through with statusText
    }
    throw new AuthError(res.status, detail, scope);
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

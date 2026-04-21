import { type FormEvent, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { AuthError } from '../lib/authApi';

type FormErrorKind = 'error' | 'warning';

export function Signup() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<{ kind: FormErrorKind; msg: string } | null>(null);

  const { signup } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (password.length < 8) {
      setFormError({ kind: 'error', msg: 'Password must be at least 8 characters' });
      return;
    }
    setSubmitting(true);
    try {
      await signup(email, password);
      navigate('/', { replace: true });
    } catch (err) {
      // 429 from signup rate-limit is a soft "try again later" — render it as
      // a yellow warning rather than a red error so real users aren't scared.
      if (err instanceof AuthError && err.status === 429 && err.rateLimitScope) {
        setFormError({ kind: 'warning', msg: err.message });
      } else {
        const msg = err instanceof Error ? err.message : 'Signup failed';
        setFormError({ kind: 'error', msg });
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg)] text-[var(--text-primary)] p-4">
      <div className="flex flex-col items-center mb-4">
        <img src="/logo.svg" alt="DynaChat logo" className="w-10 h-10 mb-2" />
        <span className="text-xl font-semibold text-[var(--text-primary)]">DynaChat</span>
        <span className="text-sm text-[var(--text-secondary)]">Ask Cole Medin's YouTube library anything</span>
      </div>
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-[var(--surface-1)] border border-[var(--border)] rounded-lg p-6 space-y-4"
      >
        <h1 className="text-xl font-semibold">Create account</h1>
        <label className="block text-sm">
          <span className="text-[var(--text-secondary)]">Email</span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded bg-[var(--surface-2)] border border-[var(--border)] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
          />
        </label>
        <label className="block text-sm">
          <span className="text-[var(--text-secondary)]">Password (8+ characters)</span>
          <input
            type="password"
            required
            autoComplete="new-password"
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full px-3 py-2 rounded bg-[var(--surface-2)] border border-[var(--border)] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
          />
        </label>
        {formError && formError.kind === 'error' && (
          <div className="text-sm text-[var(--danger)]" role="alert">
            {formError.msg}
          </div>
        )}
        {formError && formError.kind === 'warning' && (
          <div
            className="text-sm rounded px-3 py-2 border"
            role="alert"
            style={{
              color: 'var(--warning)',
              backgroundColor: 'var(--warning-bg)',
              borderColor: 'var(--warning-border)',
            }}
          >
            {formError.msg}
          </div>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="w-full py-2 rounded bg-[var(--accent)] text-white font-medium disabled:opacity-50"
        >
          {submitting ? 'Creating account…' : 'Sign up'}
        </button>
        <div className="text-sm text-[var(--text-secondary)] text-center">
          Already have an account?{' '}
          <Link to="/login" className="text-[var(--accent)] hover:underline">
            Log in
          </Link>
        </div>
      </form>
    </div>
  );
}

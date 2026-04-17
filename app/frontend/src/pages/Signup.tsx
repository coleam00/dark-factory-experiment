import { FormEvent, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export function Signup() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const { signup } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (password.length < 8) {
      setFormError('Password must be at least 8 characters');
      return;
    }
    setSubmitting(true);
    try {
      await signup(email, password);
      navigate('/', { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Signup failed';
      setFormError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg)] text-[var(--text-primary)] p-4">
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
        {formError && (
          <div className="text-sm text-[var(--danger)]" role="alert">
            {formError}
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

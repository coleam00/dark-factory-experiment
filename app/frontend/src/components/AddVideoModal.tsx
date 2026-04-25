import { type FormEvent, useEffect, useRef, useState } from 'react';

interface AddVideoModalProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (url: string) => Promise<void>;
}

export function AddVideoModal({ open, onClose, onSubmit }: AddVideoModalProps) {
  const [url, setUrl] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setUrl('');
      setError(null);
      setSubmitting(false);
      inputRef.current?.focus();
    }
  }, [open]);

  if (!open) return null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(url.trim());
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add video');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Add video by URL"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.6)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
      }}
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
        className="w-full max-w-md bg-[var(--surface-1)] border border-[var(--border)] rounded-lg p-6 space-y-4 shadow-2xl"
      >
        <h2 className="text-base font-semibold">Add video by URL</h2>
        <label className="block text-sm">
          <span className="text-[var(--text-secondary)]">YouTube URL</span>
          <input
            ref={inputRef}
            type="url"
            required
            placeholder="https://www.youtube.com/watch?v=..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={submitting}
            className="mt-1 w-full px-3 py-2 rounded bg-[var(--surface-2)] border border-[var(--border)] text-[var(--text-primary)] outline-none focus:border-[var(--accent)] disabled:opacity-60"
          />
        </label>
        {error && (
          <div className="text-sm text-[var(--danger)]" role="alert">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="px-3 py-2 rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting || !url.trim()}
            className="px-3 py-2 rounded bg-[var(--accent)] text-white font-medium disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--surface-1)]"
          >
            {submitting ? 'Adding…' : 'Add video'}
          </button>
        </div>
      </form>
    </div>
  );
}

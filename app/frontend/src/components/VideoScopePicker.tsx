import { useEffect, useState } from 'react';
import { type Video, getVideos } from '../lib/api';

interface VideoScopePickerProps {
  open: boolean;
  initialSelected?: string[];
  onConfirm: (ids: string[]) => void;
  onClose: () => void;
}

export function VideoScopePicker({
  open,
  initialSelected,
  onConfirm,
  onClose,
}: VideoScopePickerProps) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!open) return;
    setFilter('');
    setSelected(new Set(initialSelected ?? []));
    setError(null);
    setLoading(true);
    getVideos()
      .then((data) => setVideos(data))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load videos'))
      .finally(() => setLoading(false));
    // initialSelected is intentionally only read when the picker opens.
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const query = filter.trim().toLowerCase();
  const visible = query
    ? videos.filter(
        (v) =>
          v.title.toLowerCase().includes(query) ||
          (v.channel_title ?? '').toLowerCase().includes(query),
      )
    : videos;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Focus on specific videos"
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
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md bg-[var(--surface-1)] border border-[var(--border)] rounded-lg p-6 space-y-4 shadow-2xl"
      >
        <h2 className="text-lg font-semibold">Focus on specific videos</h2>
        <p className="text-sm text-[var(--text-secondary)]">
          The assistant will only answer using the videos you pick. The selection is permanent for
          this conversation.
        </p>
        <input
          type="text"
          placeholder="Filter videos…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-full px-3 py-2 rounded bg-[var(--surface-2)] border border-[var(--border)] text-[var(--text-primary)] outline-none focus:border-[var(--accent)]"
        />
        <div className="max-h-72 overflow-y-auto space-y-1" data-testid="video-scope-list">
          {loading && <p className="text-sm text-[var(--text-secondary)]">Loading videos…</p>}
          {error && (
            <p className="text-sm text-[var(--danger)]" role="alert">
              {error}
            </p>
          )}
          {!loading && !error && visible.length === 0 && (
            <p className="text-sm text-[var(--text-secondary)]">No videos match.</p>
          )}
          {!loading &&
            !error &&
            visible.map((v) => (
              <label
                key={v.id}
                className="flex items-start gap-2 px-2 py-1.5 rounded cursor-pointer hover:bg-[var(--surface-2)]"
              >
                <input
                  type="checkbox"
                  checked={selected.has(v.id)}
                  onChange={() => toggle(v.id)}
                  className="mt-1"
                />
                <span className="text-sm">
                  <span className="block text-[var(--text-primary)]">{v.title}</span>
                  {v.channel_title && (
                    <span className="block text-xs text-[var(--text-secondary)]">
                      {v.channel_title}
                    </span>
                  )}
                </span>
              </label>
            ))}
        </div>
        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            onClick={() => setSelected(new Set())}
            disabled={selected.size === 0}
            className="text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50"
          >
            Clear selection
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-2 rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => onConfirm(Array.from(selected))}
              disabled={selected.size === 0}
              className="px-3 py-2 rounded bg-[var(--accent)] text-white font-medium disabled:opacity-50 transition-[filter] duration-150 active:brightness-90 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            >
              {selected.size > 0
                ? `Focus on ${selected.size} video${selected.size === 1 ? '' : 's'}`
                : 'Focus'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

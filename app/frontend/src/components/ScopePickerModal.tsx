import { useCallback, useEffect, useMemo, useState } from 'react';
import { type Video, getVideos } from '../lib/api';

// ── Scope picker modal ────────────────────────────────────────────
// Lets a user restrict a conversation to a subset of videos (issue #279).
// `currentScope` is the conversation's existing scope (null = all videos);
// pre-checks matching items. `onConfirm` receives the selected ids, or `null`
// when the user clears the scope (no videos selected → search everything).
interface ScopePickerModalProps {
  currentScope: string[] | null;
  onConfirm: (videoIds: string[] | null) => void;
  onCancel: () => void;
}

export function ScopePickerModal({ currentScope, onConfirm, onCancel }: ScopePickerModalProps) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set(currentScope ?? []));
  const [query, setQuery] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getVideos()
      .then((data) => {
        if (!cancelled) setVideos(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load videos');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onCancel]);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      q
        ? videos.filter((v) =>
            [v.title, v.channel_title, v.description]
              .filter(Boolean)
              .join(' ')
              .toLowerCase()
              .includes(q),
          )
        : videos,
    [videos, q],
  );

  const handleConfirm = () => {
    // No selection → clear the scope (search everything). Otherwise restrict.
    onConfirm(selected.size === 0 ? null : Array.from(selected));
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-label="Scope conversation to videos"
        aria-modal="true"
        className="bg-slate-800 border border-white/10 rounded-xl w-[460px] max-w-[calc(100vw-48px)] max-h-[80vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-white/10">
          <div>
            <h3 className="text-slate-100 text-base font-semibold m-0">Scope to videos</h3>
            <p className="mt-1 text-xs text-slate-400 leading-relaxed">
              The assistant will only answer using the selected videos. Select none to search the
              whole library.
            </p>
          </div>
          <button
            onClick={onCancel}
            aria-label="Close"
            className="bg-none border-none text-slate-400 cursor-pointer text-lg leading-none ml-3 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          >
            ×
          </button>
        </div>

        {/* Search + selection summary */}
        <div className="px-5 py-3 border-b border-white/10 flex items-center gap-3">
          <input
            type="search"
            placeholder="Search videos…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="flex-1 p-2 bg-slate-900 border border-white/10 rounded-md text-slate-100 text-sm box-border outline-none focus:border-blue-500 transition-colors"
            aria-label="Search videos"
          />
          <button
            onClick={() => setSelected(new Set())}
            disabled={selected.size === 0}
            className="text-xs text-slate-400 whitespace-nowrap cursor-pointer bg-transparent border-none disabled:opacity-40 hover:text-slate-200"
          >
            Clear scope
          </button>
        </div>

        {/* Video list */}
        <div className="flex-1 overflow-y-auto px-5 py-3 flex flex-col gap-2">
          {loading && <p className="text-slate-400 text-sm py-4 text-center">Loading videos…</p>}
          {!loading && error && (
            <p className="text-red-400 text-sm py-4 text-center">{error}</p>
          )}
          {!loading && !error && videos.length === 0 && (
            <p className="text-slate-500 text-sm py-4 text-center">
              No videos in the knowledge base yet.
            </p>
          )}
          {!loading &&
            !error &&
            filtered.map((video) => {
              const checked = selected.has(video.id);
              return (
                <label
                  key={video.id}
                  className="flex items-start gap-3 p-2.5 rounded-lg border border-white/10 bg-slate-900 cursor-pointer hover:border-blue-500/40 transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(video.id)}
                    className="mt-0.5 cursor-pointer accent-blue-500"
                  />
                  <span className="flex flex-col min-w-0">
                    <span className="text-sm text-slate-100 leading-tight truncate">
                      {video.title}
                    </span>
                    {video.channel_title && (
                      <span className="text-xs text-slate-500 mt-0.5 truncate">
                        {video.channel_title}
                      </span>
                    )}
                  </span>
                </label>
              );
            })}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-white/10">
          <span className="text-xs text-slate-400">
            {selected.size === 0 ? 'All videos' : `${selected.size} selected`}
          </span>
          <div className="flex gap-2">
            <button
              onClick={onCancel}
              className="px-4 py-2 bg-transparent border border-white/20 rounded-md text-slate-300 text-sm cursor-pointer focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              className="px-4 py-2 bg-blue-500 border-none rounded-md text-white text-sm cursor-pointer focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            >
              Save scope
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState } from 'react';
import { type Video, getVideos } from '../lib/api';

interface VideoScopePickerProps {
  /** Currently selected video ids. */
  selected: string[];
  /** Called with the new selection whenever the user toggles a video. */
  onChange: (videoIds: string[]) => void;
}

/**
 * Optional video picker shown on the landing screen (issue #279). Lets the user
 * scope a new conversation to a subset of videos before sending their first
 * message — the assistant then only answers from, and only cites, those videos.
 *
 * Picking nothing leaves the conversation unscoped (searches the whole
 * library), which is the default behaviour. The panel is collapsed by default
 * so users who never want scoping aren't distracted by it.
 */
export function VideoScopePicker({ selected, onChange }: VideoScopePickerProps) {
  const [open, setOpen] = useState(false);
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  // Lazy-load the video list the first time the panel is opened so the landing
  // screen stays cheap for users who never scope.
  useEffect(() => {
    if (!open || loaded || loading) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getVideos()
      .then((data) => {
        if (!cancelled) {
          setVideos(data);
          setLoaded(true);
        }
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
  // `loading` is intentionally omitted from deps: it is set inside the effect
  // so including it would cause an infinite re-fetch loop on error (loading
  // goes false → effect re-triggers → another fetch starts → repeat).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, loaded]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const toggle = useCallback(
    (id: string) => {
      if (selectedSet.has(id)) {
        onChange(selected.filter((v) => v !== id));
      } else {
        onChange([...selected, id]);
      }
    },
    [selected, selectedSet, onChange],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return videos;
    return videos.filter((v) => v.title.toLowerCase().includes(q));
  }, [videos, query]);

  const count = selected.length;

  return (
    <div className="w-full max-w-md mx-auto text-left">
      <div className="flex items-center gap-2 w-full px-3 py-2 bg-slate-800 border border-white/10 rounded-lg">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-2 flex-1 text-sm text-slate-300 hover:text-slate-100 transition-colors text-left"
          aria-expanded={open}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 14 14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`transition-transform ${open ? 'rotate-90' : ''}`}
          >
            <polyline points="5,3 9,7 5,11" />
          </svg>
          <span className="flex-1">
            {count > 0
              ? `Scoped to ${count} video${count === 1 ? '' : 's'}`
              : 'Scope to specific videos (optional)'}
          </span>
        </button>
        {count > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="text-xs text-slate-400 hover:text-slate-100 underline cursor-pointer shrink-0"
          >
            Clear
          </button>
        )}
      </div>

      {open && (
        <div className="mt-2 bg-slate-800 border border-white/10 rounded-lg p-3">
          <p className="text-xs text-slate-400 mb-2 leading-relaxed">
            Pick the videos this conversation should draw from. The assistant will only answer using
            — and only cite — the videos you select. Leave empty to search everything.
          </p>

          {(loaded || loading) && (
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter videos…"
              className="w-full px-2.5 py-1.5 mb-2 bg-slate-900 border border-white/10 rounded-md text-sm text-slate-100 placeholder:text-slate-500 focus:outline-none focus:border-blue-500/50"
            />
          )}

          {loading && <p className="text-xs text-slate-400 py-2">Loading videos…</p>}
          {error && <p className="text-xs text-red-400 py-2">{error}</p>}

          {loaded && filtered.length === 0 && (
            <p className="text-xs text-slate-400 py-2">No videos match.</p>
          )}

          {loaded && filtered.length > 0 && (
            <ul className="max-h-56 overflow-y-auto flex flex-col gap-0.5">
              {filtered.map((v) => {
                const checked = selectedSet.has(v.id);
                return (
                  <li key={v.id}>
                    <label className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-700/60 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggle(v.id)}
                        className="accent-blue-500 shrink-0"
                      />
                      <span className="text-sm text-slate-200 truncate">{v.title}</span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

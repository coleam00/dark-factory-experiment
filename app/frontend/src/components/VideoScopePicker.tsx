import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { type Video, getVideos } from '../lib/api';

// ── Highlight matched substring in a video title ─────────────────
function highlightMatch(title: string, query: string): string | React.ReactElement {
  if (!title) return title;
  const q = query.trim();
  if (!q) return title;
  const idx = title.toLowerCase().indexOf(q.toLowerCase());
  if (idx === -1) return title;
  return (
    <>
      {title.slice(0, idx)}
      <mark className="bg-blue-500/35 text-inherit p-0 rounded-sm">
        {title.slice(idx, idx + q.length)}
      </mark>
      {title.slice(idx + q.length)}
    </>
  );
}

// ── Skeleton row ─────────────────────────────────────────────────
function SkeletonRow() {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <div className="skeleton h-4 w-4 rounded-sm flex-shrink-0" />
      <div className="skeleton h-3.5 w-3/4" />
    </div>
  );
}

interface VideoScopePickerProps {
  open: boolean;
  initialSelected?: string[];
  onConfirm: (ids: string[]) => void;
  onClose: () => void;
}

export function VideoScopePicker({
  open,
  initialSelected = [],
  onConfirm,
  onClose,
}: VideoScopePickerProps) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set(initialSelected));
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');

  const fetchVideos = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getVideos();
      setVideos(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load videos');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchVideos();
      setSelected(new Set(initialSelected));
      setSearchQuery('');
      setDebouncedQuery('');
    }
  }, [open, initialSelected, fetchVideos]);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 250);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  const q = debouncedQuery.trim().toLowerCase();
  const filteredVideos = q
    ? videos.filter((v) =>
        [v.title, v.channel_title, v.description]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(q),
      )
    : videos;

  const toggleVideo = (id: string) => {
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

  const handleConfirm = () => {
    onConfirm(Array.from(selected));
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
      <div className="bg-slate-800 border border-white/10 rounded-xl w-[460px] max-w-[calc(100vw-48px)] max-h-[80vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 flex-shrink-0">
          <h3 className="text-slate-100 text-base font-semibold m-0">Focus on specific videos</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="bg-transparent border-none text-slate-400 cursor-pointer text-lg focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          >
            ×
          </button>
        </div>

        {/* Search */}
        <div className="px-5 py-3 border-b border-white/10 flex-shrink-0">
          <input
            type="search"
            placeholder="Search videos…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full p-2 bg-slate-900 border border-white/10 rounded-md text-slate-100 text-sm box-border outline-none focus:border-blue-500 transition-colors"
            aria-label="Search videos"
          />
        </div>

        {/* Video list */}
        <div className="flex-1 overflow-y-auto px-5 py-2">
          {loading && (
            <>
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </>
          )}

          {!loading && error && (
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <svg
                width="32"
                height="32"
                viewBox="0 0 32 32"
                fill="none"
                stroke="#ef4444"
                strokeWidth="1.5"
                strokeLinecap="round"
              >
                <circle cx="16" cy="16" r="14" />
                <line x1="16" y1="9" x2="16" y2="17" />
                <circle cx="16" cy="22" r="1" fill="#ef4444" stroke="none" />
              </svg>
              <p className="m-0 text-red-500 text-sm">Failed to load videos</p>
              <button
                onClick={fetchVideos}
                className="bg-slate-800 border border-white/10 rounded-lg text-slate-100 cursor-pointer px-5 py-2 text-sm focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
              >
                Retry
              </button>
            </div>
          )}

          {!loading && !error && videos.length === 0 && (
            <div className="py-8 text-center text-slate-500 text-sm">
              No videos in the knowledge base yet.
            </div>
          )}

          {!loading && !error && videos.length > 0 && filteredVideos.length === 0 && (
            <div className="py-8 text-center text-slate-500 text-sm">
              No videos match &ldquo;{debouncedQuery}&rdquo;
            </div>
          )}

          {!loading &&
            !error &&
            filteredVideos.map((video) => (
              <label
                key={video.id}
                className="flex items-start gap-3 py-2.5 cursor-pointer hover:bg-white/5 rounded-md px-1 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selected.has(video.id)}
                  onChange={() => toggleVideo(video.id)}
                  className="mt-0.5 flex-shrink-0"
                />
                <span className="text-sm text-slate-100 leading-snug">
                  {highlightMatch(video.title, debouncedQuery)}
                </span>
              </label>
            ))}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-4 border-t border-white/10 flex-shrink-0">
          <button
            onClick={() => setSelected(new Set())}
            className="text-slate-400 text-sm hover:text-slate-100 transition-colors cursor-pointer bg-transparent border-none"
          >
            Clear
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-transparent border border-white/20 rounded-md text-slate-400 text-sm cursor-pointer focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            >
              Cancel
            </button>
            <button
              onClick={handleConfirm}
              disabled={selected.size === 0}
              className="px-4 py-2 bg-blue-500 border-none rounded-md text-white text-sm cursor-pointer disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            >
              Use {selected.size} video{selected.size === 1 ? '' : 's'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

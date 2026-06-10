import { useCallback, useEffect, useState } from 'react';
import { type Conversation, type Video, createConversation, getVideos } from '../lib/api';

// ── Video scope picker (issue #279) ───────────────────────────────
// Modal multi-select that creates a NEW conversation scoped to a subset of
// videos. "Search all videos" (the default) creates an unscoped conversation
// — identical to the plain New Chat path. Scope is immutable after creation,
// so this only ever runs at conversation-creation time.
interface VideoScopePickerProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called with the freshly created conversation so the parent can refetch
   *  the sidebar list and navigate to it. */
  onCreated: (conv: Conversation) => void;
}

export function VideoScopePicker({ isOpen, onClose, onCreated }: VideoScopePickerProps) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

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

  // Load videos and reset selection each time the picker opens.
  useEffect(() => {
    if (isOpen) {
      setSelectedIds(new Set());
      setSearchQuery('');
      setCreateError(null);
      fetchVideos();
    }
  }, [isOpen, fetchVideos]);

  // Close on Escape.
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const toggle = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const q = searchQuery.trim().toLowerCase();
  const filteredVideos = q
    ? videos.filter((v) =>
        [v.title, v.channel_title, v.description]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(q),
      )
    : videos;

  const scopeAll = selectedIds.size === 0;

  const handleCreate = async () => {
    setCreating(true);
    setCreateError(null);
    try {
      // No selection → unscoped (search-all) conversation. Otherwise scope to
      // the selected ids.
      const ids = scopeAll ? undefined : Array.from(selectedIds);
      const conv = await createConversation(ids);
      onCreated(conv);
      onClose();
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Failed to create conversation.');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Scope conversation to videos"
        className="bg-slate-800 border border-white/10 rounded-xl p-6 w-[460px] max-w-[calc(100vw-48px)] max-h-[80vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex justify-between items-center mb-2">
          <h3 className="text-slate-100 text-base font-semibold m-0">Scope to videos</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="bg-none border-none text-slate-400 cursor-pointer text-lg focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          >
            ×
          </button>
        </div>
        <p className="text-xs text-slate-400 mb-3 leading-relaxed">
          Pick the videos this conversation should draw from. Leave everything unselected to search
          the whole library. This choice is locked in once the conversation starts.
        </p>

        {/* Search */}
        {!loading && !error && videos.length > 0 && (
          <input
            type="search"
            placeholder="Filter videos…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Filter videos"
            className="w-full p-2 mb-3 bg-slate-900 border border-white/10 rounded-md text-slate-100 text-sm box-border outline-none focus:border-blue-500 transition-colors"
          />
        )}

        {/* Video list */}
        <div className="flex-1 overflow-y-auto -mx-1 px-1 flex flex-col gap-1.5">
          {loading && <p className="py-6 text-center text-slate-500 text-sm">Loading videos…</p>}

          {!loading && error && (
            <div className="py-6 text-center">
              <p className="m-0 text-red-500 text-sm mb-2">{error}</p>
              <button
                onClick={fetchVideos}
                className="bg-slate-700 border border-white/10 rounded-md text-slate-100 cursor-pointer px-4 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
              >
                Retry
              </button>
            </div>
          )}

          {!loading && !error && videos.length === 0 && (
            <p className="py-6 text-center text-slate-500 text-sm">
              No videos in the library yet.
            </p>
          )}

          {!loading && !error && videos.length > 0 && filteredVideos.length === 0 && (
            <p className="py-6 text-center text-slate-500 text-sm">
              No videos match &ldquo;{searchQuery}&rdquo;
            </p>
          )}

          {!loading &&
            !error &&
            filteredVideos.map((video) => (
              <label
                key={video.id}
                className="flex items-start gap-2.5 p-2 rounded-md cursor-pointer hover:bg-slate-700/60 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.has(video.id)}
                  onChange={() => toggle(video.id)}
                  className="mt-0.5 accent-blue-500 cursor-pointer"
                />
                <span className="flex flex-col min-w-0">
                  <span className="text-sm text-slate-100 leading-tight truncate">
                    {video.title}
                  </span>
                  {video.channel_title && (
                    <span className="text-xs text-slate-400 truncate">{video.channel_title}</span>
                  )}
                </span>
              </label>
            ))}
        </div>

        {createError && <p className="text-red-400 mt-3 text-sm">{createError}</p>}

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 mt-4 pt-3 border-t border-white/10">
          <span className="text-xs text-slate-400" data-testid="scope-summary">
            {scopeAll ? 'Searching all videos' : `Scoped to ${selectedIds.size} video${selectedIds.size === 1 ? '' : 's'}`}
          </span>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="px-4 py-2 bg-transparent border border-white/20 rounded-md text-slate-400 text-sm cursor-pointer focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            >
              Cancel
            </button>
            <button
              onClick={handleCreate}
              disabled={creating || loading}
              className="px-4 py-2 bg-blue-500 border-none rounded-md text-white text-sm cursor-pointer disabled:opacity-75 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            >
              {creating ? 'Creating…' : 'Start chat'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

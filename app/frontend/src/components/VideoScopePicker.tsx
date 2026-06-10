import { useEffect, useState } from 'react';
import { type Video, getVideos, updateConversationScope } from '../lib/api';

interface VideoScopePickerProps {
  conversationId: string;
  /** Currently active scope — null/undefined means "all videos". */
  current: string[] | null;
  onClose: () => void;
  /** Called after a successful save with the new scope (null = cleared). */
  onSaved: (ids: string[] | null) => void;
}

export function VideoScopePicker({
  conversationId,
  current,
  onClose,
  onSaved,
}: VideoScopePickerProps) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(() => new Set(current ?? []));

  useEffect(() => {
    getVideos()
      .then(setVideos)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load videos'))
      .finally(() => setLoading(false));
  }, []);

  // Close on ESC key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

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

  const save = async (ids: string[] | null) => {
    setSaving(true);
    setError(null);
    try {
      const conv = await updateConversationScope(conversationId, ids);
      onSaved(conv.scoped_video_ids ?? null);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save scope');
    } finally {
      setSaving(false);
    }
  };

  const handleApply = () => {
    // An empty selection is the same as clearing — the backend stores NULL,
    // never an empty array.
    const ids = Array.from(selected);
    save(ids.length > 0 ? ids : null);
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Scope conversation to videos"
    >
      <div
        className="bg-slate-800 border border-white/10 rounded-xl p-6 w-[520px] max-w-[calc(100vw-48px)] max-h-[80vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex justify-between items-center mb-1">
          <h3 className="text-slate-100 text-base font-semibold m-0">Choose videos</h3>
          <button
            onClick={onClose}
            className="bg-none border-none text-slate-400 cursor-pointer text-xl leading-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <p className="text-slate-400 text-xs m-0 mb-4">
          Answers and citations in this conversation will only come from the selected videos.
        </p>

        {/* Video list */}
        <div className="flex-1 min-h-0 overflow-y-auto mb-4 flex flex-col gap-1">
          {loading && <p className="text-slate-400 text-sm m-0">Loading videos…</p>}
          {!loading && error && <p className="text-red-400 text-sm m-0">{error}</p>}
          {!loading && !error && videos.length === 0 && (
            <p className="text-slate-400 text-sm m-0">No videos in the library yet.</p>
          )}
          {!loading &&
            videos.map((v) => (
              <label
                key={v.id}
                className="flex items-start gap-2.5 px-2 py-2 rounded-lg cursor-pointer hover:bg-slate-700/50"
              >
                <input
                  type="checkbox"
                  checked={selected.has(v.id)}
                  onChange={() => toggle(v.id)}
                  className="mt-0.5 accent-blue-500"
                />
                <span className="text-slate-200 text-sm leading-snug">{v.title}</span>
              </label>
            ))}
        </div>

        {/* Footer actions */}
        <div className="flex justify-between items-center gap-3">
          <button
            onClick={() => save(null)}
            disabled={saving}
            className="bg-transparent border border-white/10 rounded-lg text-slate-300 cursor-pointer px-4 py-2 text-sm hover:bg-slate-700/50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Search all videos
          </button>
          <div className="flex items-center gap-3">
            <span className="text-slate-400 text-xs">
              {selected.size > 0 ? `${selected.size} selected` : 'None selected'}
            </span>
            <button
              onClick={handleApply}
              disabled={saving || loading}
              className="bg-blue-500 border-none rounded-lg text-white cursor-pointer px-4 py-2 text-sm font-medium hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saving ? 'Saving…' : 'Apply'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

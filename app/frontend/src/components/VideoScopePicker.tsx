import { useEffect, useState } from 'react';
import { type Video, getVideos } from '../lib/api';

interface VideoScopePickerProps {
  /** Video ids currently in the conversation's scope (empty = all videos). */
  selectedIds: string[];
  /**
   * Called with the chosen video ids when the user saves. An empty selection
   * is emitted as null, meaning "all videos" (clears the scope).
   */
  onSave: (ids: string[] | null) => void;
  onClose: () => void;
}

/**
 * Modal that lets the user scope a conversation to a subset of the video
 * library (issue #279). Loads the library via getVideos() and presents a
 * checkbox per video. "All videos" clears the scope; "Save" applies the
 * checked set. Styled to match CitationModal (Tailwind primitives).
 */
export function VideoScopePicker({ selectedIds, onSave, onClose }: VideoScopePickerProps) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(() => new Set(selectedIds));

  useEffect(() => {
    let cancelled = false;
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

  // Close on ESC key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  // Lock body scroll while modal is open
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  const toggle = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleSave = () => {
    const ids = Array.from(checked);
    // Empty selection means "all videos" — clear the scope.
    onSave(ids.length > 0 ? ids : null);
  };

  const handleAllVideos = () => {
    onSave(null);
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
        className="bg-slate-800 border border-white/10 rounded-xl p-6 w-[560px] max-w-[calc(100vw-48px)] max-h-[90vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex justify-between items-center mb-1">
          <h3 className="text-slate-100 text-base font-semibold m-0">Scope this conversation</h3>
          <button
            onClick={onClose}
            className="bg-none border-none text-slate-400 cursor-pointer text-xl leading-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <p className="text-slate-400 text-xs m-0 mb-4">
          Pick which videos the assistant should answer from. Leave all unchecked to search the
          whole library.
        </p>

        {/* Video list */}
        <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-1 mb-4">
          {loading && <p className="text-slate-400 text-sm m-0">Loading videos…</p>}
          {error && <p className="text-red-400 text-sm m-0">{error}</p>}
          {!loading && !error && videos.length === 0 && (
            <p className="text-slate-400 text-sm m-0">No videos in the library yet.</p>
          )}
          {!loading &&
            !error &&
            videos.map((v) => (
              <label
                key={v.id}
                className="flex items-start gap-3 p-2 rounded-lg cursor-pointer hover:bg-slate-700/50"
              >
                <input
                  type="checkbox"
                  checked={checked.has(v.id)}
                  onChange={() => toggle(v.id)}
                  className="mt-1 cursor-pointer"
                />
                <span className="flex flex-col">
                  <span className="text-slate-100 text-sm">{v.title}</span>
                  {v.channel_title ? (
                    <span className="text-slate-400 text-xs">{v.channel_title}</span>
                  ) : null}
                </span>
              </label>
            ))}
        </div>

        {/* Footer actions */}
        <div className="flex justify-between items-center">
          <button
            onClick={handleAllVideos}
            className="text-slate-400 hover:text-slate-200 text-xs transition-colors bg-transparent border-none cursor-pointer"
          >
            All videos
          </button>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="text-slate-300 text-sm px-3 py-1.5 rounded-lg border border-white/10 bg-transparent hover:bg-slate-700/50 cursor-pointer transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              className="text-white text-sm px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 border-none cursor-pointer transition-colors"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

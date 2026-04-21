import { useCallback, useEffect, useState } from 'react';
import { type Video, getVideos, ingestVideo } from '../lib/api';
import { useAuth } from '../hooks/useAuth';

// ── Skeleton card ────────────────────────────────────────────────
function SkeletonCard() {
  return (
    <div className="bg-slate-800 border border-white/10 rounded-lg p-3.5">
      <div className="skeleton h-3.5 w-3/5 mb-2.5" />
      <div className="skeleton h-2.5 w-9/10 mb-1.5" />
      <div className="skeleton h-2.5 w-3/4" />
    </div>
  );
}

// ── Video card ───────────────────────────────────────────────────
function VideoCard({ video }: { video: Video }) {
  return (
    <div
      className="bg-slate-800 border border-white/10 rounded-lg p-3.5 transition-colors duration-150"
      onMouseEnter={(e) => e.currentTarget.classList.add('video-card-hover')}
      onMouseLeave={(e) => e.currentTarget.classList.remove('video-card-hover')}
    >
      {/* Title */}
      <p className="text-sm font-semibold text-slate-100 mb-1.5 leading-tight">{video.title}</p>

      {/* Description */}
      {video.description && (
        <p className="text-xs text-slate-400 mb-2 leading-relaxed">
          {video.description.length > 120
            ? video.description.slice(0, 117) + '…'
            : video.description}
        </p>
      )}

      {/* URL link */}
      {video.url && (
        <a
          href={video.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-blue-500 no-underline inline-flex items-center gap-1"
          onMouseEnter={(e) => (e.currentTarget.style.textDecoration = 'underline')}
          onMouseLeave={(e) => (e.currentTarget.style.textDecoration = 'none')}
        >
          <svg
            width="11"
            height="11"
            viewBox="0 0 11 11"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M5,1 H9.5 V5.5" />
            <line x1="9.5" y1="1" x2="2" y2="8.5" />
            <path d="M2,3 H1 V10 H8 V9" />
          </svg>
          Watch on YouTube
        </a>
      )}
    </div>
  );
}

// ── Main VideoExplorer panel ──────────────────────────────────────
interface VideoExplorerProps {
  isOpen: boolean;
  onClose: () => void;
}

const INGEST_FIELDS = [
  { key: 'title', label: 'Title', placeholder: 'Video title', type: 'text' },
  { key: 'description', label: 'Description', placeholder: 'Short description', type: 'text' },
  {
    key: 'url',
    label: 'YouTube URL',
    placeholder: 'https://www.youtube.com/watch?v=...',
    type: 'url',
  },
  {
    key: 'transcript',
    label: 'Transcript',
    placeholder: 'Full transcript text...',
    type: 'textarea',
  },
] as const;

export function VideoExplorer({ isOpen, onClose }: VideoExplorerProps) {
  const { user } = useAuth();
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [ingestForm, setIngestForm] = useState({
    title: '',
    description: '',
    url: '',
    transcript: '',
  });
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);

  const closeDialog = () => {
    setIngestOpen(false);
    setIngestError(null);
  };

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

  const handleIngest = async () => {
    if (!ingestForm.title || !ingestForm.description || !ingestForm.url || !ingestForm.transcript) {
      setIngestError('All fields are required.');
      return;
    }
    setIngesting(true);
    setIngestError(null);
    try {
      await ingestVideo(ingestForm);
      const updated = await getVideos();
      setVideos(updated);
      setIngestOpen(false);
      setIngestForm({ title: '', description: '', url: '', transcript: '' });
    } catch (e) {
      setIngestError(e instanceof Error ? e.message : 'Failed to add video.');
    } finally {
      setIngesting(false);
    }
  };

  // Load videos when panel opens
  useEffect(() => {
    if (isOpen && videos.length === 0 && !loading && !error) {
      fetchVideos();
    }
  }, [isOpen, videos.length, loading, error, fetchVideos]);

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  return (
    <>
      {/* Backdrop */}
      {isOpen && <div onClick={onClose} className="fixed inset-0 bg-black/50 z-30" />}

      {/* Slide-over panel */}
      <div
        role="dialog"
        aria-label="Video Knowledge Base"
        aria-modal="true"
        className="fixed top-0 right-0 h-full w-[380px] max-w-[90vw] bg-gray-900 border-l border-white/10 z-40 flex flex-col transition-transform duration-300 shadow-[-8px_0_32px_rgba(0,0,0,0.4)]"
        style={{ transform: isOpen ? 'translateX(0)' : 'translateX(100%)' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 flex-shrink-0">
          <div>
            <h2 className="m-0 text-base font-semibold text-slate-100">Video Library</h2>
            {!loading && videos.length > 0 && (
              <p className="mt-0.5 text-xs text-slate-400">
                {videos.length} videos in knowledge base
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close video library"
            className="bg-transparent border border-white/10 rounded-lg text-slate-400 cursor-pointer p-2 flex items-center justify-center transition-colors duration-150 mr-2"
            onMouseEnter={(e) => {
              e.currentTarget.classList.remove('text-slate-400', 'bg-transparent');
              e.currentTarget.classList.add('text-slate-100', 'bg-slate-800');
            }}
            onMouseLeave={(e) => {
              e.currentTarget.classList.remove('text-slate-100', 'bg-slate-800');
              e.currentTarget.classList.add('text-slate-400', 'bg-transparent');
            }}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <line x1="3" y1="3" x2="11" y2="11" />
              <line x1="11" y1="3" x2="3" y2="11" />
            </svg>
          </button>
          {user?.is_admin && (
            <button
              onClick={() => setIngestOpen(true)}
              className="px-3 py-1.5 bg-blue-500 border-none rounded-md text-white text-sm cursor-pointer"
              title="Add new video"
            >
              + Add Video
            </button>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-2.5">
          {loading && (
            <>
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
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
              <p className="m-0 text-slate-600 text-xs">{error}</p>
              <button
                onClick={fetchVideos}
                className="bg-slate-800 border border-white/10 rounded-lg text-slate-100 cursor-pointer px-5 py-2 text-sm"
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

          {!loading && !error && videos.map((video) => <VideoCard key={video.id} video={video} />)}
        </div>

        {/* Ingest dialog */}
        {ingestOpen && (
          <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
            <div className="bg-slate-800 border border-white/10 rounded-xl p-6 w-[420px] max-w-[calc(100vw-48px)]">
              <div className="flex justify-between items-center mb-4">
                <h3 className="text-slate-100 text-base font-semibold m-0">Add New Video</h3>
                <button
                  onClick={closeDialog}
                  className="bg-none border-none text-slate-400 cursor-pointer text-lg"
                >
                  ×
                </button>
              </div>
              {ingestError && <p className="text-red-400 mb-3 text-sm">{ingestError}</p>}
              {INGEST_FIELDS.map(({ key, label, placeholder, type }) => (
                <div key={key} className="mb-3">
                  <label htmlFor={key} className="block text-slate-400 text-xs mb-1">
                    {label}
                  </label>
                  {type === 'textarea' ? (
                    <textarea
                      id={key}
                      value={ingestForm[key as keyof typeof ingestForm]}
                      onChange={(e) => setIngestForm({ ...ingestForm, [key]: e.target.value })}
                      placeholder={placeholder}
                      rows={4}
                      className="w-full p-2 bg-slate-900 border border-white/10 rounded-md text-slate-100 text-sm box-border resize-y"
                    />
                  ) : (
                    <input
                      id={key}
                      type={type}
                      value={ingestForm[key as keyof typeof ingestForm]}
                      onChange={(e) => setIngestForm({ ...ingestForm, [key]: e.target.value })}
                      placeholder={placeholder}
                      className="w-full p-2 bg-slate-900 border border-white/10 rounded-md text-slate-100 text-sm box-border"
                    />
                  )}
                </div>
              ))}
              <div className="flex gap-2 justify-end mt-4">
                <button
                  onClick={closeDialog}
                  className="px-4 py-2 bg-transparent border border-white/20 rounded-md text-slate-400 text-sm cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  onClick={handleIngest}
                  disabled={ingesting}
                  className="px-4 py-2 bg-blue-500 border-none rounded-md text-white text-sm cursor-pointer disabled:opacity-75"
                >
                  {ingesting ? 'Adding…' : 'Add Video'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

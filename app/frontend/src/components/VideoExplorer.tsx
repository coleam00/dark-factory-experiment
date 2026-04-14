import { useCallback, useEffect, useState } from 'react';
import { type Video, getVideos } from '../lib/api';

// ── Skeleton card ────────────────────────────────────────────────
function SkeletonCard() {
  return (
    <div
      style={{
        background: '#1e293b',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 10,
        padding: '14px 16px',
      }}
    >
      <div className="skeleton" style={{ height: 14, width: '65%', marginBottom: 10 }} />
      <div className="skeleton" style={{ height: 11, width: '90%', marginBottom: 6 }} />
      <div className="skeleton" style={{ height: 11, width: '75%' }} />
    </div>
  );
}

// ── Video card ───────────────────────────────────────────────────
function VideoCard({ video }: { video: Video }) {
  return (
    <div
      style={{
        background: '#1e293b',
        border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: 10,
        padding: '14px 16px',
        transition: 'border-color 0.15s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'rgba(59,130,246,0.3)')}
      onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'rgba(255,255,255,0.06)')}
    >
      {/* Title */}
      <p
        style={{
          margin: '0 0 6px',
          fontSize: 14,
          fontWeight: 600,
          color: '#f1f5f9',
          lineHeight: 1.4,
        }}
      >
        {video.title}
      </p>

      {/* Description */}
      {video.description && (
        <p
          style={{
            margin: '0 0 8px',
            fontSize: 13,
            color: '#94a3b8',
            lineHeight: 1.5,
          }}
        >
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
          style={{
            fontSize: 12,
            color: '#3b82f6',
            textDecoration: 'none',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
          }}
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

export function VideoExplorer({ isOpen, onClose }: VideoExplorerProps) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      {isOpen && (
        <div
          onClick={onClose}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.5)',
            zIndex: 30,
          }}
        />
      )}

      {/* Slide-over panel */}
      <div
        role="dialog"
        aria-label="Video Knowledge Base"
        aria-modal="true"
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          height: '100vh',
          width: 380,
          maxWidth: '90vw',
          background: '#111827',
          borderLeft: '1px solid rgba(255,255,255,0.08)',
          zIndex: 40,
          display: 'flex',
          flexDirection: 'column',
          transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.25s ease',
          boxShadow: '-8px 0 32px rgba(0,0,0,0.4)',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '16px 20px',
            borderBottom: '1px solid rgba(255,255,255,0.06)',
            flexShrink: 0,
          }}
        >
          <div>
            <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: '#f1f5f9' }}>
              Video Library
            </h2>
            {!loading && videos.length > 0 && (
              <p style={{ margin: '2px 0 0', fontSize: 12, color: '#94a3b8' }}>
                {videos.length} videos in knowledge base
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close video library"
            style={{
              background: 'transparent',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 8,
              color: '#94a3b8',
              cursor: 'pointer',
              padding: 8,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'color 0.15s, background 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = '#f1f5f9';
              e.currentTarget.style.background = '#1e293b';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = '#94a3b8';
              e.currentTarget.style.background = 'transparent';
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
        </div>

        {/* Content */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '16px 20px',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          {loading && (
            <>
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
              <SkeletonCard />
            </>
          )}

          {!loading && error && (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 12,
                padding: '32px 0',
                textAlign: 'center',
              }}
            >
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
              <p style={{ margin: 0, color: '#ef4444', fontSize: 14 }}>Failed to load videos</p>
              <p style={{ margin: 0, color: '#475569', fontSize: 13 }}>{error}</p>
              <button
                onClick={fetchVideos}
                style={{
                  background: '#1e293b',
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 8,
                  color: '#f1f5f9',
                  cursor: 'pointer',
                  padding: '8px 20px',
                  fontSize: 14,
                }}
              >
                Retry
              </button>
            </div>
          )}

          {!loading && !error && videos.length === 0 && (
            <div
              style={{
                padding: '32px 0',
                textAlign: 'center',
                color: '#475569',
                fontSize: 14,
              }}
            >
              No videos in the knowledge base yet.
            </div>
          )}

          {!loading && !error && videos.map((video) => <VideoCard key={video.id} video={video} />)}
        </div>
      </div>
    </>
  );
}

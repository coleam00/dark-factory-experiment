import { useEffect } from 'react';
import type { Citation } from '../lib/api';

interface CitationModalProps {
  citation: Citation;
  onClose: () => void;
}

export function formatTimestamp(seconds: number): string {
  const s = Math.floor(seconds);
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function CitationModal({ citation, onClose }: CitationModalProps) {
  // Modal is YouTube-only. Dynamous citations are routed directly to lesson_url
  // by the caller (ChatArea.handleCitationClick) — this component never sees them.

  // Extract YouTube video ID from URL (format: https://www.youtube.com/watch?v=<id>)
  let videoId = '';
  try {
    videoId = new URL(citation.video_url).searchParams.get('v') ?? '';
  } catch {
    console.warn('[CitationModal] Could not parse video URL:', citation.video_url);
  }

  const startSeconds = Math.floor(citation.start_seconds);

  const embedUrl = videoId
    ? `https://www.youtube.com/embed/${videoId}?start=${startSeconds}&autoplay=1`
    : '';

  const externalUrl = videoId
    ? `https://www.youtube.com/watch?v=${videoId}&t=${startSeconds}s`
    : '';

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

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center"
      onClick={(e) => {
        // Close when clicking the backdrop (not the dialog itself)
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Video citation"
    >
      <div
        className="bg-slate-800 border border-white/10 rounded-xl p-6 w-[640px] max-w-[calc(100vw-48px)] max-h-[90vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex justify-between items-center mb-4">
          <div>
            <h3 className="text-slate-100 text-base font-semibold m-0">{citation.video_title}</h3>
            <p className="text-slate-400 text-xs m-0 mt-0.5">
              at {formatTimestamp(citation.start_seconds)} – {formatTimestamp(citation.end_seconds)}
            </p>
          </div>
          <button
            onClick={onClose}
            className="bg-none border-none text-slate-400 cursor-pointer text-xl leading-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {/* Content: YouTube iframe. Modal is YouTube-only — Dynamous citations
            go directly to lesson_url (handled in ChatArea before this modal opens). */}
        <div className="flex-1 min-h-0 flex flex-col gap-4 mb-4 overflow-y-auto">
          <div className="w-full aspect-video">
            {embedUrl ? (
              <iframe
                src={embedUrl}
                allow="autoplay; encrypted-media"
                allowFullScreen
                title="YouTube video player"
                className="w-full h-full rounded-lg"
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center bg-slate-900 rounded-lg text-slate-500 text-sm">
                Video unavailable
              </div>
            )}
          </div>
        </div>

        {/* Footer: external link */}
        <div className="flex justify-end">
          {externalUrl ? (
            <a
              href={externalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-400 hover:text-slate-200 text-xs flex items-center gap-1 transition-colors"
            >
              Open on YouTube
              <svg
                width="10"
                height="10"
                viewBox="0 0 10 10"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M1 9L9 1M9 1H3M9 1v6" />
              </svg>
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}

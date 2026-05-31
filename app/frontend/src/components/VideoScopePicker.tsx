import { useEffect, useState } from 'react';
import { type Video, getVideos } from '../lib/api';

// ── Video scope picker (issue #279) ───────────────────────────────
// Multi-select list shown in the new-chat empty state. The selection is
// captured BEFORE the conversation is created (conversations are created
// lazily on first send), then sent to createConversation(). An empty
// selection means "search everything" — the conversation stays unscoped.
//
// Controlled component: the parent owns `selectedIds` and is notified via
// `onChange`. Styling follows the surrounding ChatArea/Sidebar convention
// (inline style objects rather than Tailwind classes).
interface VideoScopePickerProps {
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}

export function VideoScopePicker({ selectedIds, onChange }: VideoScopePickerProps) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getVideos()
      .then((vids) => {
        if (!cancelled) {
          setVideos(vids);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError('Could not load videos');
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = (id: string) => {
    if (selectedIds.includes(id)) {
      onChange(selectedIds.filter((v) => v !== id));
    } else {
      onChange([...selectedIds, id]);
    }
  };

  const clear = () => onChange([]);

  const summary =
    selectedIds.length === 0
      ? 'All videos'
      : `${selectedIds.length} video${selectedIds.length === 1 ? '' : 's'}`;

  return (
    <div style={{ width: '100%', maxWidth: 400, marginBottom: 16 }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          padding: '8px 12px',
          background: '#1e293b',
          border: '1px solid rgba(255,255,255,0.08)',
          borderRadius: 8,
          color: '#94a3b8',
          fontSize: 13,
          cursor: 'pointer',
        }}
      >
        <span>
          Scope:{' '}
          <strong style={{ color: '#f1f5f9', fontWeight: 600 }}>{summary}</strong>
        </span>
        <span style={{ fontSize: 11, opacity: 0.8 }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div
          style={{
            marginTop: 6,
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 8,
            background: '#0f172a',
            maxHeight: 220,
            overflowY: 'auto',
          }}
        >
          {loading && (
            <p style={{ padding: '12px', margin: 0, fontSize: 13, color: '#475569' }}>
              Loading videos…
            </p>
          )}
          {error && (
            <p style={{ padding: '12px', margin: 0, fontSize: 13, color: '#ef4444' }}>{error}</p>
          )}
          {!loading && !error && videos.length === 0 && (
            <p style={{ padding: '12px', margin: 0, fontSize: 13, color: '#475569' }}>
              No videos available.
            </p>
          )}
          {!loading &&
            !error &&
            videos.map((v) => {
              const checked = selectedIds.includes(v.id);
              return (
                <label
                  key={v.id}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 12px',
                    fontSize: 13,
                    color: '#f1f5f9',
                    cursor: 'pointer',
                    borderBottom: '1px solid rgba(255,255,255,0.04)',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggle(v.id)}
                    style={{ flexShrink: 0, cursor: 'pointer' }}
                  />
                  <span
                    style={{
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {v.title}
                  </span>
                </label>
              );
            })}
        </div>
      )}

      {selectedIds.length > 0 && (
        <button
          type="button"
          onClick={clear}
          style={{
            marginTop: 6,
            background: 'transparent',
            border: 'none',
            color: '#3b82f6',
            cursor: 'pointer',
            fontSize: 12,
            padding: 0,
          }}
        >
          Clear selection (search all videos)
        </button>
      )}
    </div>
  );
}

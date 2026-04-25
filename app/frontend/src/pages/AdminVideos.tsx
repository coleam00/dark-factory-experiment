/**
 * Admin video library management.
 *
 * Re-reads the list after every mutation rather than surgically patching local
 * state — chunk_count changes after re-sync, and sync-channel can add many
 * rows at once. Authoritative list is cheaper than local bookkeeping.
 */

import { useCallback, useEffect, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { AddVideoModal } from '../components/AddVideoModal';
import { useAdminVideos } from '../hooks/useAdminVideos';
import { useAuth } from '../hooks/useAuth';
import { useToast } from '../hooks/useToast';
import { type AdminVideo, addVideoByUrl, deleteVideo, resyncVideo, syncChannel } from '../lib/api';

export function AdminVideos() {
  const { status, user } = useAuth();
  const { addToast } = useToast();
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const { videos, loading, refetch } = useAdminVideos(debouncedQuery);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [addModalOpen, setAddModalOpen] = useState(false);

  // Debounce search query — 250ms per issue #92 pattern
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 250);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Guard rendering
  if (status === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--bg)] text-[var(--text-secondary)]">
        Loading…
      </div>
    );
  }
  if (status === 'anon') {
    return <Navigate to="/login" replace state={{ from: '/admin' }} />;
  }
  if (!user?.is_admin) {
    return <Navigate to="/" replace />;
  }

  async function handleAdd(url: string) {
    const res = await addVideoByUrl(url);
    addToast(`Added video (${res.chunks_created} chunks)`, 'success');
    await refetch();
  }

  async function handleDelete(video: AdminVideo) {
    if (!confirm(`Delete "${video.title}" and its ${video.chunk_count} chunks?`)) {
      return;
    }
    setPendingId(video.id);
    try {
      await deleteVideo(video.id);
      addToast('Video deleted', 'success');
      await refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Delete failed', 'error');
    } finally {
      setPendingId(null);
    }
  }

  async function handleResync(video: AdminVideo) {
    setPendingId(video.id);
    try {
      const res = await resyncVideo(video.id);
      addToast(`Re-synced (${res.chunks_created} chunks)`, 'success');
      await refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Re-sync failed', 'error');
    } finally {
      setPendingId(null);
    }
  }

  async function handleSyncChannel() {
    setSyncing(true);
    try {
      const res = await syncChannel();
      addToast(
        `Channel sync ${res.status}: ${res.videos_new} new, ${res.videos_error} errors`,
        res.status === 'completed' ? 'success' : 'error',
      );
      await refetch();
    } catch (err) {
      addToast(err instanceof Error ? err.message : 'Channel sync failed', 'error');
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--text-primary)] p-6">
      <div className="max-w-5xl mx-auto">
        <header className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold">Library admin</h1>
            <p className="text-sm text-[var(--text-secondary)] mt-1">
              Add, re-sync, or remove videos in the RAG corpus.
            </p>
          </div>
          <Link to="/" className="text-sm text-[var(--accent)] hover:underline">
            ← Back to chat
          </Link>
        </header>

        <div className="flex gap-2 mb-4">
          <button
            type="button"
            onClick={() => setAddModalOpen(true)}
            disabled={syncing}
            className="px-3 py-2 rounded bg-[var(--accent)] text-white font-medium disabled:opacity-50"
          >
            + Add video by URL
          </button>
          <button
            type="button"
            onClick={handleSyncChannel}
            disabled={syncing}
            className="px-3 py-2 rounded border border-[var(--border)] text-[var(--text-primary)] hover:bg-[var(--surface-2)] disabled:opacity-50"
          >
            {syncing ? 'Syncing channel…' : 'Sync channel'}
          </button>
          <button
            type="button"
            onClick={refetch}
            disabled={loading || syncing}
            className="px-3 py-2 rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50"
          >
            Refresh
          </button>
        </div>

        <div style={{ marginBottom: 12 }}>
          <input
            type="text"
            placeholder="Search videos..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              width: '100%',
              padding: '8px 12px',
              borderRadius: 8,
              background: '#1e293b',
              border: '1px solid #334155',
              color: '#f1f5f9',
              fontSize: 13,
              outline: 'none',
              transition: 'border-color 0.15s',
            }}
            onFocus={(e) => (e.currentTarget.style.borderColor = '#3b82f6')}
            onBlur={(e) => (e.currentTarget.style.borderColor = '#334155')}
          />
        </div>

        <div className="bg-[var(--surface-1)] border border-[var(--border)] rounded-lg overflow-hidden">
          {loading ? (
            <div className="p-6 text-center text-[var(--text-secondary)]">Loading…</div>
          ) : videos.length === 0 ? (
            <div className="p-6 text-center text-[var(--text-secondary)]">
              {debouncedQuery.trim()
                ? `No matches for "${debouncedQuery}"`
                : 'No videos yet. Add one by URL or sync a channel.'}
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead className="bg-[var(--surface-2)] text-left">
                <tr>
                  <th className="px-4 py-2 font-medium">Title</th>
                  <th className="px-4 py-2 font-medium">Chunks</th>
                  <th className="px-4 py-2 font-medium">Added</th>
                  <th className="px-4 py-2 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {videos.map((v) => {
                  const isPending = pendingId === v.id;
                  return (
                    <tr key={v.id} className="border-t border-[var(--border)]">
                      <td className="px-4 py-2">
                        <a
                          href={v.url}
                          target="_blank"
                          rel="noreferrer"
                          className="hover:underline"
                        >
                          {v.title || '(untitled)'}
                        </a>
                      </td>
                      <td className="px-4 py-2 text-[var(--text-secondary)]">{v.chunk_count}</td>
                      <td className="px-4 py-2 text-[var(--text-secondary)]">
                        {v.created_at.slice(0, 10)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => handleResync(v)}
                          disabled={isPending || syncing}
                          className="px-2 py-1 text-xs rounded border border-[var(--border)] hover:bg-[var(--surface-2)] disabled:opacity-50 mr-2"
                        >
                          {isPending ? '…' : 'Re-sync'}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(v)}
                          disabled={isPending || syncing}
                          className="px-2 py-1 text-xs rounded border border-[var(--danger)] text-[var(--danger)] hover:bg-[var(--surface-2)] disabled:opacity-50"
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <AddVideoModal
        open={addModalOpen}
        onClose={() => setAddModalOpen(false)}
        onSubmit={handleAdd}
      />
    </div>
  );
}

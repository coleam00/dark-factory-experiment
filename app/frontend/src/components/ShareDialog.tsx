import { useEffect, useRef, useState } from 'react';
import { createShareLink, revokeShareLink, type ShareLinkResponse } from '../lib/api';
import { useToast } from '../hooks/useToast';

interface ShareDialogProps {
  conversationId: string;
  open: boolean;
  onClose: () => void;
}

export function ShareDialog({ conversationId, open, onClose }: ShareDialogProps) {
  const [link, setLink] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const { addToast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setLink(null);
      setLoading(false);
      setRevoking(false);
    }
  }, [open]);

  if (!open) return null;

  async function handleCreate() {
    setLoading(true);
    try {
      const res: ShareLinkResponse = await createShareLink(conversationId);
      const url = `${window.location.origin}${res.url_path}`;
      setLink(url);
      addToast('Share link created', 'success');
      // Auto-select the input so user can Ctrl+C immediately
      setTimeout(() => {
        inputRef.current?.select();
      }, 50);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to create share link';
      addToast(msg, 'error');
    } finally {
      setLoading(false);
    }
  }

  async function handleCopy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      addToast('Copied to clipboard', 'success');
    } catch {
      addToast('Copy failed — select and copy manually', 'error');
    }
  }

  async function handleRevoke() {
    setRevoking(true);
    try {
      await revokeShareLink(conversationId);
      setLink(null);
      addToast('Share link revoked', 'success');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to revoke share link';
      addToast(msg, 'error');
    } finally {
      setRevoking(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Share conversation"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.6)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md bg-[var(--surface-1)] border border-[var(--border)] rounded-lg p-6 space-y-4 shadow-2xl"
      >
        <h2 className="text-lg font-semibold">Share conversation</h2>
        <p className="text-sm text-[var(--text-secondary)]">
          Anyone with the link can view this conversation read-only. They won&apos;t be able to send
          messages or see your other chats.
        </p>

        {!link ? (
          <button
            onClick={handleCreate}
            disabled={loading}
            className="w-full px-3 py-2 rounded bg-[var(--accent)] text-white font-medium disabled:opacity-50 transition-[filter] duration-150 active:brightness-90 focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          >
            {loading ? 'Creating…' : 'Create share link'}
          </button>
        ) : (
          <div className="space-y-3">
            <div className="flex gap-2">
              <input
                ref={inputRef}
                readOnly
                value={link}
                className="flex-1 px-3 py-2 rounded bg-[var(--surface-2)] border border-[var(--border)] text-[var(--text-primary)] outline-none focus:border-[var(--accent)] text-sm"
              />
              <button
                onClick={handleCopy}
                className="px-3 py-2 rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none text-sm"
              >
                Copy
              </button>
            </div>
            <button
              onClick={handleRevoke}
              disabled={revoking}
              className="w-full px-3 py-2 rounded border border-red-500/50 text-red-400 hover:text-red-300 hover:bg-red-500/10 disabled:opacity-50 transition-colors text-sm font-medium focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:outline-none"
            >
              {revoking ? 'Revoking…' : 'Revoke link'}
            </button>
          </div>
        )}

        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-2 rounded border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { useConversations } from '../hooks/useConversations';
import { type Conversation, createConversation, deleteConversation } from '../lib/api';
import { VideoExplorer } from './VideoExplorer';

// ── Relative time helper ─────────────────────────────────────────
function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHrs = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHrs / 24);

  if (diffSecs < 60) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHrs < 24) return `${diffHrs}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// ── Skeleton row ─────────────────────────────────────────────────
function SkeletonRow() {
  return (
    <div style={{ padding: '12px 16px', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
      <div className="skeleton" style={{ height: 14, width: '70%', marginBottom: 8 }} />
      <div className="skeleton" style={{ height: 11, width: '40%', marginBottom: 6 }} />
      <div className="skeleton" style={{ height: 11, width: '90%' }} />
    </div>
  );
}

// ── Single conversation item ─────────────────────────────────────
interface ConvItemProps {
  conv: Conversation;
  isActive: boolean;
  onSelect: () => void;
  onDeleteRequest: (id: string) => void;
}

function ConvItem({ conv, isActive, onSelect, onDeleteRequest }: ConvItemProps) {
  const [hovered, setHovered] = useState(false);

  const preview = conv.preview
    ? conv.preview.length > 80
      ? conv.preview.slice(0, 77) + '…'
      : conv.preview
    : null;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => e.key === 'Enter' && onSelect()}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: 'relative',
        padding: '10px 16px',
        cursor: 'pointer',
        borderBottom: '1px solid rgba(255,255,255,0.04)',
        background: isActive ? '#1e293b' : hovered ? 'rgba(30,41,59,0.6)' : 'transparent',
        borderLeft: isActive ? '3px solid #3b82f6' : '3px solid transparent',
        paddingLeft: 13,
        transition: 'background 0.15s',
        userSelect: 'none',
      }}
    >
      {/* Title */}
      <div
        style={{
          fontSize: 14,
          fontWeight: 500,
          color: '#f1f5f9',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          paddingRight: hovered ? 28 : 0,
        }}
      >
        {conv.title}
      </div>

      {/* Timestamp */}
      <div
        style={{
          fontSize: 12,
          color: '#94a3b8',
          marginTop: 2,
          marginBottom: preview ? 3 : 0,
        }}
      >
        {formatRelativeTime(conv.updated_at)}
      </div>

      {/* Preview */}
      {preview && (
        <div
          style={{
            fontSize: 12,
            color: '#475569',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {preview}
        </div>
      )}

      {/* Delete button — visible on hover */}
      {hovered && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDeleteRequest(conv.id);
          }}
          title="Delete conversation"
          style={{
            position: 'absolute',
            right: 10,
            top: '50%',
            transform: 'translateY(-50%)',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: '#475569',
            padding: 4,
            borderRadius: 4,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: 'color 0.15s',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = '#ef4444')}
          onMouseLeave={(e) => (e.currentTarget.style.color = '#475569')}
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 14 14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          >
            <polyline points="2,4 12,4" />
            <path d="M5,4V2.5a.5.5,0,0,1,.5-.5h3a.5.5,0,0,1,.5.5V4" />
            <path d="M3,4l.7,7.5a.5.5,0,0,0,.5.5h5.6a.5.5,0,0,0,.5-.5L11,4" />
          </svg>
        </button>
      )}
    </div>
  );
}

// ── Delete confirm dialog ─────────────────────────────────────────
interface ConfirmDialogProps {
  onConfirm: () => void;
  onCancel: () => void;
  deleting: boolean;
  error: boolean;
}

function ConfirmDialog({ onConfirm, onCancel, deleting, error }: ConfirmDialogProps) {
  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.6)',
        zIndex: 50,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      <div
        style={{
          background: '#1e293b',
          border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: 12,
          padding: 24,
          width: 320,
          maxWidth: 'calc(100vw - 48px)',
        }}
      >
        <p style={{ margin: '0 0 8px', fontWeight: 600, color: '#f1f5f9' }}>Delete conversation?</p>
        <p style={{ margin: '0 0 20px', fontSize: 14, color: '#94a3b8' }}>
          This action cannot be undone.
        </p>
        {error && (
          <p style={{ margin: '0 0 16px', fontSize: 13, color: '#ef4444' }}>
            Failed to delete. Please try again.
          </p>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            style={{
              background: 'transparent',
              border: '1px solid rgba(255,255,255,0.15)',
              borderRadius: 8,
              color: '#94a3b8',
              cursor: 'pointer',
              padding: '8px 16px',
              fontSize: 14,
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={deleting}
            style={{
              background: '#ef4444',
              border: 'none',
              borderRadius: 8,
              color: '#fff',
              cursor: deleting ? 'not-allowed' : 'pointer',
              padding: '8px 16px',
              fontSize: 14,
              opacity: deleting ? 0.7 : 1,
            }}
          >
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Daily quota counter ──────────────────────────────────────────
interface DailyQuotaCounterProps {
  used: number;
  remaining: number;
  resetsAt: string | null;
}

function DailyQuotaCounter({ used, remaining, resetsAt }: DailyQuotaCounterProps) {
  const cap = used + remaining;
  const atLimit = remaining === 0;
  const resetLabel = resetsAt
    ? new Date(resetsAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
    : null;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="quota-counter"
      style={{
        padding: '8px 12px',
        borderTop: '1px solid rgba(255,255,255,0.06)',
        fontSize: 12,
        color: atLimit ? '#ef4444' : '#94a3b8',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: 8,
      }}
    >
      <span>
        <strong style={{ color: atLimit ? '#ef4444' : '#f1f5f9', fontWeight: 600 }}>
          {used}/{cap}
        </strong>{' '}
        messages today
      </span>
      {atLimit && resetLabel && (
        <span style={{ fontSize: 11, opacity: 0.85 }}>resets at {resetLabel}</span>
      )}
    </div>
  );
}

// ── Main Sidebar component ───────────────────────────────────────
interface SidebarProps {
  activeConversationId?: string;
  isOpen: boolean;
  onClose: () => void;
}

export function Sidebar({ activeConversationId, isOpen, onClose }: SidebarProps) {
  const navigate = useNavigate();
  const { conversations, loading, refetch } = useConversations();
  const { user } = useAuth();
  const [creatingNew, setCreatingNew] = useState(false);
  const [newChatError, setNewChatError] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(false);
  const [explorerOpen, setExplorerOpen] = useState(false);

  // ── New Chat ──
  const handleNewChat = async () => {
    setCreatingNew(true);
    setNewChatError(null);
    try {
      const conv = await createConversation();
      await refetch();
      navigate(`/c/${conv.id}`);
      onClose();
    } catch (e) {
      setNewChatError('Could not create conversation. Please try again.');
    } finally {
      setCreatingNew(false);
    }
  };

  // ── Delete flow ──
  const handleDeleteRequest = (id: string) => {
    setConfirmId(id);
    setDeleteError(false);
  };

  const handleDeleteConfirm = async () => {
    if (!confirmId) return;
    setDeleting(true);
    setDeleteError(false);
    try {
      const res = await deleteConversation(confirmId);
      if (!res.ok && res.status !== 204) {
        throw new Error('Delete failed');
      }
      setConfirmId(null);
      await refetch();
      if (activeConversationId === confirmId) {
        navigate('/');
      }
    } catch {
      setDeleteError(true);
    } finally {
      setDeleting(false);
    }
  };

  const handleDeleteCancel = () => {
    setConfirmId(null);
    setDeleteError(false);
  };

  // ── Navigate to conversation ──
  const handleSelect = (id: string) => {
    navigate(`/c/${id}`);
    onClose();
  };

  return (
    <>
      <aside className={`sidebar-container${isOpen ? ' open' : ''}`}>
        {/* ── New Chat button ── */}
        <div style={{ padding: '12px 12px 8px' }}>
          <button
            onClick={handleNewChat}
            disabled={creatingNew}
            style={{
              width: '100%',
              background: '#3b82f6',
              color: '#fff',
              border: 'none',
              borderRadius: 8,
              padding: '10px 16px',
              cursor: creatingNew ? 'not-allowed' : 'pointer',
              fontWeight: 600,
              fontSize: 14,
              opacity: creatingNew ? 0.75 : 1,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              transition: 'background 0.15s',
            }}
            onMouseEnter={(e) => !creatingNew && (e.currentTarget.style.background = '#1d4ed8')}
            onMouseLeave={(e) => !creatingNew && (e.currentTarget.style.background = '#3b82f6')}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
            >
              <line x1="7" y1="2" x2="7" y2="12" />
              <line x1="2" y1="7" x2="12" y2="7" />
            </svg>
            {creatingNew ? 'Creating…' : 'New Chat'}
          </button>

          {newChatError && (
            <p style={{ fontSize: 12, color: '#ef4444', margin: '8px 0 0', textAlign: 'center' }}>
              {newChatError}
            </p>
          )}
        </div>

        {/* ── Conversation list ── */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading ? (
            <>
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
              <SkeletonRow />
            </>
          ) : conversations.length === 0 ? (
            // Empty state
            <div
              style={{
                padding: '40px 16px',
                textAlign: 'center',
                color: '#475569',
              }}
            >
              <svg
                width="36"
                height="36"
                viewBox="0 0 36 36"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                style={{ margin: '0 auto 12px', display: 'block', opacity: 0.5 }}
              >
                <path d="M6,4 L30,4 A2,2 0 0,1 32,6 L32,24 A2,2 0 0,1 30,26 L10,26 L4,32 L4,6 A2,2 0 0,1 6,4 Z" />
              </svg>
              <p style={{ margin: 0, fontSize: 13 }}>No conversations yet</p>
              <button
                onClick={handleNewChat}
                style={{
                  marginTop: 10,
                  background: 'transparent',
                  border: '1px solid rgba(59,130,246,0.4)',
                  borderRadius: 8,
                  color: '#3b82f6',
                  cursor: 'pointer',
                  fontSize: 13,
                  padding: '7px 16px',
                  transition: 'background 0.15s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(59,130,246,0.1)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
              >
                Start your first chat →
              </button>
            </div>
          ) : (
            conversations.map((conv) => (
              <ConvItem
                key={conv.id}
                conv={conv}
                isActive={conv.id === activeConversationId}
                onSelect={() => handleSelect(conv.id)}
                onDeleteRequest={handleDeleteRequest}
              />
            ))
          )}
        </div>

        {/* ── Daily message quota counter (MISSION §10 #1: hardcoded 25/24h) ── */}
        {user && (
          <DailyQuotaCounter
            used={user.messages_used_today}
            remaining={user.messages_remaining_today}
            resetsAt={user.rate_window_resets_at}
          />
        )}

        {/* ── Sidebar footer: branding + library button ── */}
        <div
          style={{
            padding: '10px 12px',
            borderTop: '1px solid rgba(255,255,255,0.06)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span style={{ fontSize: 12, color: '#475569' }}>RAG YouTube Chat</span>

          {/* Library / VideoExplorer button */}
          <button
            onClick={() => setExplorerOpen(true)}
            title="Browse video library"
            aria-label="Browse video library"
            style={{
              background: 'transparent',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 7,
              color: '#94a3b8',
              cursor: 'pointer',
              padding: '5px 7px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 5,
              fontSize: 12,
              transition: 'background 0.15s, color 0.15s, border-color 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = '#1e293b';
              e.currentTarget.style.color = '#f1f5f9';
              e.currentTarget.style.borderColor = 'rgba(59,130,246,0.4)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = 'transparent';
              e.currentTarget.style.color = '#94a3b8';
              e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)';
            }}
          >
            {/* Play/video icon */}
            <svg
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="1" y="2" width="12" height="10" rx="2" />
              <polygon points="5,4.5 9.5,7 5,9.5" fill="currentColor" stroke="none" />
            </svg>
            Library
          </button>
        </div>
      </aside>

      {/* ── Confirm delete dialog ── */}
      {confirmId && (
        <ConfirmDialog
          onConfirm={handleDeleteConfirm}
          onCancel={handleDeleteCancel}
          deleting={deleting}
          error={deleteError}
        />
      )}

      {/* ── Video Explorer panel ── */}
      <VideoExplorer isOpen={explorerOpen} onClose={() => setExplorerOpen(false)} />
    </>
  );
}

import { type MutableRefObject, useCallback, useEffect, useRef, useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import { useMessages } from '../hooks/useMessages';
import { useStreamingResponse } from '../hooks/useStreamingResponse';
import { useToast } from '../hooks/useToast';
import type { Citation, Message as MessageType } from '../lib/api';
import { RateLimitError } from '../lib/api';
import { exportConversationAsMarkdown } from '../lib/exportMarkdown';
import { ChatInput, type ChatInputHandle } from './ChatInput';
import { CitationModal } from './CitationModal';
import { Message } from './Message';

function formatResetTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) || iso;
}

// ── Skeleton message rows ─────────────────────────────────────────
function SkeletonMessages() {
  const rows: Array<{ align: 'flex-end' | 'flex-start'; widths: string[] }> = [
    { align: 'flex-end', widths: ['60%', '40%'] },
    { align: 'flex-start', widths: ['75%', '55%', '70%'] },
    { align: 'flex-end', widths: ['45%'] },
    { align: 'flex-start', widths: ['80%', '60%'] },
  ];

  return (
    <>
      {rows.map((row, ri) => (
        <div
          key={ri}
          style={{
            display: 'flex',
            justifyContent: row.align,
            marginBottom: 12,
            padding: '0 24px',
          }}
        >
          <div
            style={{
              maxWidth: row.align === 'flex-end' ? '70%' : '80%',
              width: '100%',
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
            }}
          >
            {row.widths.map((w, wi) => (
              <div key={wi} className="skeleton" style={{ height: 16, width: w }} />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

// ── Empty / landing state ─────────────────────────────────────────
interface EmptyStateProps {
  onStarterClick: (text: string) => void;
}

function EmptyState({ onStarterClick }: EmptyStateProps) {
  const starters = [
    'How do RAG pipelines work?',
    'What are the best prompt engineering practices?',
    'Explain vector databases and embeddings',
    'What is the difference between fine-tuning and prompting?',
  ];

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        flex: 1,
        padding: '40px 24px',
        textAlign: 'center',
      }}
    >
      <svg
        width="56"
        height="56"
        viewBox="0 0 56 56"
        fill="none"
        stroke="#3b82f6"
        strokeWidth="1.5"
        style={{ marginBottom: 20, opacity: 0.7 }}
      >
        <circle cx="28" cy="28" r="24" />
        <path d="M18,22 L38,22 M18,28 L34,28 M18,34 L30,34" strokeLinecap="round" />
      </svg>
      <h2 style={{ margin: '0 0 8px', fontSize: 20, fontWeight: 600, color: '#f1f5f9' }}>
        Ask anything about the video library
      </h2>
      <p style={{ margin: '0 0 24px', color: '#94a3b8', maxWidth: 380, lineHeight: 1.6 }}>
        This AI has access to transcripts from a curated collection of YouTube videos.
      </p>
      <div
        style={{ display: 'flex', flexDirection: 'column', gap: 10, width: '100%', maxWidth: 400 }}
      >
        {starters.map((q) => (
          <button
            key={q}
            onClick={() => onStarterClick(q)}
            style={{
              padding: '10px 16px',
              background: '#1e293b',
              borderRadius: 10,
              border: '1px solid rgba(255,255,255,0.06)',
              color: '#94a3b8',
              fontSize: 14,
              cursor: 'pointer',
              textAlign: 'left',
              transition: 'background 0.15s, border-color 0.15s, color 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = 'rgba(30,41,59,0.9)';
              e.currentTarget.style.borderColor = 'rgba(59,130,246,0.3)';
              e.currentTarget.style.color = '#f1f5f9';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = '#1e293b';
              e.currentTarget.style.borderColor = 'rgba(255,255,255,0.06)';
              e.currentTarget.style.color = '#94a3b8';
            }}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── Load error state ──────────────────────────────────────────────
function LoadErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        flex: 1,
        padding: 40,
        textAlign: 'center',
      }}
    >
      <p style={{ color: '#ef4444', marginBottom: 16 }}>{message}</p>
      <button
        onClick={onRetry}
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
  );
}

// ── Inline send error ─────────────────────────────────────────────
interface InlineErrorProps {
  message: string;
  onRetry: () => void;
}

function InlineError({ message, onRetry }: InlineErrorProps) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        background: 'rgba(239,68,68,0.08)',
        border: '1px solid rgba(239,68,68,0.3)',
        borderRadius: 10,
        padding: '12px 16px',
        margin: '4px 0',
      }}
    >
      <svg
        width="16"
        height="16"
        viewBox="0 0 16 16"
        fill="none"
        stroke="#ef4444"
        strokeWidth="1.8"
        strokeLinecap="round"
        style={{ flexShrink: 0 }}
      >
        <circle cx="8" cy="8" r="7" />
        <line x1="8" y1="5" x2="8" y2="8.5" />
        <circle cx="8" cy="11" r="0.5" fill="#ef4444" stroke="none" />
      </svg>
      <p style={{ flex: 1, margin: 0, fontSize: 14, color: '#f1f5f9' }}>{message}</p>
      <button
        onClick={onRetry}
        style={{
          background: 'transparent',
          border: '1px solid rgba(239,68,68,0.5)',
          borderRadius: 6,
          color: '#ef4444',
          cursor: 'pointer',
          fontSize: 13,
          padding: '5px 12px',
          flexShrink: 0,
          transition: 'background 0.15s, color 0.15s',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = 'rgba(239,68,68,0.15)';
          e.currentTarget.style.color = '#f1f5f9';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'transparent';
          e.currentTarget.style.color = '#ef4444';
        }}
      >
        Retry
      </button>
    </div>
  );
}

// ── Main ChatArea component ───────────────────────────────────────
interface ChatAreaProps {
  conversationId?: string;
  refreshConversationsRef?: MutableRefObject<(() => Promise<void>) | null>;
}

export function ChatArea({ conversationId, refreshConversationsRef }: ChatAreaProps) {
  const { messages, setMessages, loading, error, conversation } = useMessages(
    conversationId || null,
  );
  const { streamingContent, streamingSources, isStreaming, startStream, abortStream } =
    useStreamingResponse();
  const { addToast } = useToast();
  const { refresh: refreshAuth } = useAuth();

  const chatInputRef = useRef<ChatInputHandle>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const [, setForceUpdate] = useState(0);

  // Inline error state (for failed sends)
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [failedMessageText, setFailedMessageText] = useState<string | null>(null);
  // Track the temp user message ID so we can remove it on failure
  const pendingUserMsgIdRef = useRef<string | null>(null);
  // Citation modal state
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);

  // ── Auto-scroll logic ──
  // Defer scroll to the next paint cycle so streaming DOM updates are applied first.
  // Without rAF, scroll fires before React's DOM reconciliation completes (issue #76).
  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    bottomRef.current?.scrollIntoView({ behavior, block: 'end' });
  }, []);

  useEffect(() => {
    if (autoScrollRef.current) {
      requestAnimationFrame(() => {
        scrollToBottom();
      });
    }
  }, [messages.length, streamingContent, scrollToBottom]);

  useEffect(() => {
    if (!loading && messages.length > 0) {
      setTimeout(() => scrollToBottom('instant' as ScrollBehavior), 50);
    }
  }, [loading, scrollToBottom]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleScroll = useCallback(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const shouldAutoScroll = distFromBottom < 100;
    if (shouldAutoScroll !== autoScrollRef.current) {
      autoScrollRef.current = shouldAutoScroll;
      setForceUpdate((n) => n + 1);
    }
  }, []);

  // ── Citation click handler (opens modal) ──
  const handleCitationClick = useCallback((citation: Citation) => {
    setSelectedCitation(citation);
  }, []);

  // ── Send handler ──
  const handleSend = useCallback(
    async (content: string) => {
      if (!conversationId || isStreaming) return;

      // Clear any previous inline error
      setInlineError(null);
      setFailedMessageText(null);

      // ── Optimistic user message ──
      const tempId = `temp-user-${Date.now()}`;
      pendingUserMsgIdRef.current = tempId;

      const tempUserMsg: MessageType = {
        id: tempId,
        conversation_id: conversationId,
        role: 'user',
        content,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, tempUserMsg]);
      autoScrollRef.current = true;
      scrollToBottom();

      try {
        await startStream(conversationId, content, ({ fullText, sources }) => {
          // Append the persisted assistant message
          const assistantMsg: MessageType = {
            id: `temp-assistant-${Date.now()}`,
            conversation_id: conversationId,
            role: 'assistant',
            content: fullText,
            created_at: new Date().toISOString(),
            sources: sources.length > 0 ? sources : undefined,
          };
          setMessages((prev) => [...prev, assistantMsg]);
        });
        pendingUserMsgIdRef.current = null;
        // Pull fresh quota counter so the sidebar updates after each send.
        refreshAuth();
        // Refresh conversations list so sidebar shows auto-generated title.
        refreshConversationsRef?.current?.();
      } catch (e) {
        // Remove the optimistic user message
        if (pendingUserMsgIdRef.current) {
          const removedId = pendingUserMsgIdRef.current;
          setMessages((prev) => prev.filter((m) => m.id !== removedId));
          pendingUserMsgIdRef.current = null;
        }

        if (e instanceof RateLimitError) {
          // MISSION §10 #1 — daily cap hit. No retry; the user literally
          // can't send another message until the window slides forward.
          const friendly = `You've hit your daily message limit (${e.limit}/day). Resets at ${formatResetTime(e.resetAt)}.`;
          setInlineError(friendly);
          setFailedMessageText(null);
          addToast(friendly, 'error');
          // Sync counter so the sidebar flips to 25/25 with a reset time.
          refreshAuth();
          // Restore message text so the user can resend after the window resets.
          setTimeout(() => chatInputRef.current?.setInputText(content), 50);
          return;
        }

        const errMsg = e instanceof Error ? e.message : 'Failed to send message';
        setInlineError('Failed to get a response. Please try again.');
        setFailedMessageText(content);
        addToast(errMsg || 'Network error — message not sent', 'error');
        setTimeout(() => chatInputRef.current?.setInputText(content), 50);
      }
    },
    [
      conversationId,
      isStreaming,
      setMessages,
      startStream,
      scrollToBottom,
      addToast,
      refreshAuth,
      refreshConversationsRef,
    ],
  );

  // ── Retry failed message — re-attempt the API call with same content ──
  const handleRetry = useCallback(() => {
    if (!failedMessageText) return;
    const text = failedMessageText;
    setInlineError(null);
    setFailedMessageText(null);
    handleSend(text);
  }, [failedMessageText, handleSend]);

  // ── Starter click handler ──
  const handleStarterClick = useCallback((text: string) => {
    chatInputRef.current?.setInputText(text);
    chatInputRef.current?.focus();
  }, []);

  // ── Render ──
  const showEmpty = !conversationId;
  const showError = !loading && !!error && !showEmpty;
  const showMessages = !loading && !error && !showEmpty;
  const showSkeleton = loading && !showEmpty;

  const showStreamingBubble = isStreaming;

  const handleExport = useCallback(() => {
    try {
      if (conversation) {
        exportConversationAsMarkdown(conversation, messages);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Export failed';
      addToast(`Export failed: ${msg}`, 'error');
    }
  }, [conversation, messages, addToast]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {/* ── Message list area ── */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          padding: '24px 0 0',
          paddingBottom: 140,
          position: 'relative',
        }}
      >
        {/* ── Export button (visible when conversation is active) ── */}
        {conversation && messages.length > 0 && (
          <button
            onClick={handleExport}
            title="Export conversation as Markdown"
            style={{
              position: 'absolute',
              top: 12,
              right: 24,
              background: '#1e293b',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 7,
              color: '#94a3b8',
              cursor: 'pointer',
              padding: '5px 8px',
              display: 'flex',
              alignItems: 'center',
              gap: 5,
              fontSize: 12,
              zIndex: 5,
              transition: 'background 0.15s, color 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = '#1e293b';
              e.currentTarget.style.color = '#f1f5f9';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = '#1e293b';
              e.currentTarget.style.color = '#94a3b8';
            }}
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 13 13"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M2,9 L2,11.5 A0.5,0.5 0 0,0 2.5,12 L10.5,12 A0.5,0.5 0 0,0 11,11.5 L11,9" />
              <polyline points="6.5,1 6.5,8.5" />
              <polyline points="3.5,5.5 6.5,8.5 9.5,5.5" />
            </svg>
            Export
          </button>
        )}

        {showEmpty && <EmptyState onStarterClick={handleStarterClick} />}

        {showSkeleton && <SkeletonMessages />}

        {showError && (
          <LoadErrorState
            message={error || 'Failed to load messages'}
            onRetry={() => window.location.reload()}
          />
        )}

        {showMessages && (
          <div style={{ padding: '24px 24px 0', display: 'flex', flexDirection: 'column' }}>
            {messages.length === 0 && !inlineError ? (
              <EmptyState onStarterClick={handleStarterClick} />
            ) : (
              messages.map((msg) => (
                <Message
                  key={msg.id}
                  role={msg.role}
                  content={msg.content}
                  sources={msg.sources}
                  onCitationClick={handleCitationClick}
                />
              ))
            )}

            {/* Streaming assistant bubble */}
            {showStreamingBubble && (
              <Message
                role="assistant"
                content={streamingContent}
                isStreaming={true}
                sources={streamingSources.length > 0 ? streamingSources : undefined}
                onCitationClick={handleCitationClick}
              />
            )}

            {/* Inline send error with retry */}
            {inlineError && !isStreaming && (
              <InlineError message={inlineError} onRetry={handleRetry} />
            )}
          </div>
        )}

        <div ref={bottomRef} style={{ height: 1 }} />
      </div>

      {/* ── Gradient fade above input ── */}
      <div
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          height: 120,
          background: 'linear-gradient(to bottom, transparent, #0a0a0f)',
          pointerEvents: 'none',
          zIndex: 1,
        }}
      />

      {/* ── Chat input (only when a conversation is active) ── */}
      {conversationId && (
        <div
          style={{
            position: 'absolute',
            bottom: 0,
            left: 0,
            right: 0,
            padding: '0 24px 24px',
            zIndex: 2,
          }}
        >
          <ChatInput
            ref={chatInputRef}
            onSend={handleSend}
            isStreaming={isStreaming}
            disabled={!conversationId}
            onStop={abortStream}
          />
        </div>
      )}

      {/* ── Citation modal ── */}
      {selectedCitation && (
        <CitationModal citation={selectedCitation} onClose={() => setSelectedCitation(null)} />
      )}
    </div>
  );
}

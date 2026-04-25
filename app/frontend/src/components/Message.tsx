import { useState } from 'react';
import type { Citation } from '../lib/api';
import { MarkdownRenderer } from './MarkdownRenderer';

interface MessageProps {
  role: 'user' | 'assistant';
  content: string;
  /** When true and content is empty, renders typing indicator */
  isStreaming?: boolean;
  /** RAG citations to display below the message */
  sources?: Citation[];
  /** Called when the user clicks a citation chip */
  onCitationClick?: (citation: Citation) => void;
  /** Current tool-call status during streaming (ephemeral progress indicator) */
  streamingStatus?: { tool: string; subject: string } | null;
}

// ── Typing indicator (3 pulsing dots) ────────────────────────────
function TypingIndicator() {
  return (
    <div style={{ display: 'flex', gap: 5, alignItems: 'center', padding: '2px 0' }}>
      <div className="typing-dot" />
      <div className="typing-dot" />
      <div className="typing-dot" />
    </div>
  );
}

// ── Source citations section ──────────────────────────────────────
function formatTimestamp(seconds: number): string {
  const s = Math.floor(seconds);
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

// Citation chip; ``dimmed`` styles non-cited entries inside the consulted tier.
function CitationChip({
  citation,
  onClick,
  dimmed,
}: {
  citation: Citation;
  onClick?: (citation: Citation) => void;
  dimmed?: boolean;
}) {
  return (
    <button
      key={citation.chunk_id}
      onClick={() => onClick?.(citation)}
      title={`${citation.video_title} at ${formatTimestamp(citation.start_seconds)}\n${citation.snippet}`}
      style={{
        display: 'inline-block',
        padding: '3px 10px',
        border: dimmed ? '1px solid rgba(148,163,184,0.4)' : '1px solid #3b82f6',
        borderRadius: 20,
        fontSize: 12,
        color: dimmed ? '#94a3b8' : '#f1f5f9',
        background: dimmed ? 'rgba(148,163,184,0.06)' : 'rgba(59,130,246,0.1)',
        maxWidth: 220,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        cursor: 'pointer',
        fontFamily: 'inherit',
      }}
    >
      {formatTimestamp(citation.start_seconds)} — {citation.video_title}
    </button>
  );
}

// Two-tier source render (issue #176): chunks the LLM cited via `[c:<id>]`
// markers in Tier 1; full retrieval (collapsed) in Tier 2. Falls back to a
// single flat list when no `is_cited` flags are present (legacy messages or
// when the model forgot to emit markers).
function SourceCitations({
  sources,
  onCitationClick,
}: {
  sources: Citation[];
  onCitationClick?: (citation: Citation) => void;
}) {
  const [showConsulted, setShowConsulted] = useState(false);

  if (!sources || sources.length === 0) return null;

  const hasIsCitedField = sources.some((s) => typeof s.is_cited === 'boolean');
  const cited = sources.filter((s) => s.is_cited === true);
  const consulted = sources.filter((s) => s.is_cited !== true);
  const showTwoTier = hasIsCitedField && cited.length > 0;

  return (
    <div style={{ marginTop: 10, borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: 8 }}>
      {/* Tier 1: Sources cited (visible by default when present) */}
      {showTwoTier && (
        <>
          <div
            style={{
              color: '#94a3b8',
              fontSize: 12,
              marginBottom: 6,
              fontWeight: 500,
            }}
          >
            Sources cited ({cited.length})
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
            {cited.map((citation) => (
              <CitationChip key={citation.chunk_id} citation={citation} onClick={onCitationClick} />
            ))}
          </div>
        </>
      )}

      {/* Tier 2: All sources consulted (collapsed by default).
          When two-tier active and there's nothing extra beyond cited, skip
          the toggle entirely — Tier 1 already covers it. */}
      {(!showTwoTier || consulted.length > 0) && (
        <button
          onClick={() => setShowConsulted((v) => !v)}
          style={{
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: '#94a3b8',
            fontSize: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 5,
            padding: 0,
            transition: 'color 0.15s',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = '#f1f5f9')}
          onMouseLeave={(e) => (e.currentTarget.style.color = '#94a3b8')}
          aria-expanded={showConsulted}
          aria-label={showConsulted ? 'Collapse sources' : 'Expand sources'}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{
              transform: showConsulted ? 'rotate(90deg)' : 'rotate(0deg)',
              transition: 'transform 0.2s',
            }}
          >
            <polyline points="4,2 8,6 4,10" />
          </svg>
          {showTwoTier
            ? `All sources consulted (${sources.length})`
            : `Sources (${sources.length})`}
        </button>
      )}

      {showConsulted && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {(showTwoTier ? consulted : sources).map((citation) => (
            <CitationChip
              key={citation.chunk_id}
              citation={citation}
              onClick={onCitationClick}
              dimmed={showTwoTier}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main message component ────────────────────────────────────────
export function Message({
  role,
  content,
  isStreaming,
  sources,
  onCitationClick,
  streamingStatus,
}: MessageProps) {
  const isUser = role === 'user';
  const hasSources = !isUser && Array.isArray(sources) && sources.length > 0;

  return (
    <div
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        marginBottom: 4,
        padding: '2px 0',
      }}
    >
      <div
        style={{
          maxWidth: isUser ? '70%' : '80%',
          background: isUser ? '#2563eb' : '#1e293b',
          color: '#f1f5f9',
          borderRadius: isUser ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
          padding: '12px 16px',
          lineHeight: 1.7,
          wordBreak: 'break-word',
        }}
      >
        {isStreaming && !content ? (
          streamingStatus ? (
            <div className="text-slate-400 text-[13px] italic">
              {streamingStatus.subject ? `Searching: ${streamingStatus.subject}…` : 'Working…'}
            </div>
          ) : (
            <TypingIndicator />
          )
        ) : isUser ? (
          <span style={{ whiteSpace: 'pre-wrap' }}>{content}</span>
        ) : (
          <>
            <MarkdownRenderer content={content} />
            {hasSources && <SourceCitations sources={sources} onCitationClick={onCitationClick} />}
          </>
        )}
      </div>
    </div>
  );
}

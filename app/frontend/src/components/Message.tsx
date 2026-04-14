import { useState } from 'react';
import { MarkdownRenderer } from './MarkdownRenderer';

interface MessageProps {
  role: 'user' | 'assistant';
  content: string;
  /** When true and content is empty, renders typing indicator */
  isStreaming?: boolean;
  /** RAG source video titles to display below the message */
  sources?: string[];
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
function SourceCitations({ sources }: { sources: string[] }) {
  const [expanded, setExpanded] = useState(false);

  if (!sources || sources.length === 0) return null;

  return (
    <div style={{ marginTop: 10, borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: 8 }}>
      {/* Toggle button */}
      <button
        onClick={() => setExpanded((v) => !v)}
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
        aria-expanded={expanded}
        aria-label={expanded ? 'Collapse sources' : 'Expand sources'}
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
            transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            transition: 'transform 0.2s',
          }}
        >
          <polyline points="4,2 8,6 4,10" />
        </svg>
        Sources ({sources.length})
      </button>

      {/* Source chips */}
      {expanded && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {sources.map((title, i) => (
            <span
              key={`${title}-${i}`}
              title={title}
              style={{
                display: 'inline-block',
                padding: '3px 10px',
                border: '1px solid #3b82f6',
                borderRadius: 20,
                fontSize: 12,
                color: '#f1f5f9',
                background: 'rgba(59,130,246,0.1)',
                maxWidth: 220,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {title}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main message component ────────────────────────────────────────
export function Message({ role, content, isStreaming, sources }: MessageProps) {
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
          <TypingIndicator />
        ) : isUser ? (
          <span style={{ whiteSpace: 'pre-wrap' }}>{content}</span>
        ) : (
          <>
            <MarkdownRenderer content={content} />
            {hasSources && <SourceCitations sources={sources!} />}
          </>
        )}
      </div>
    </div>
  );
}

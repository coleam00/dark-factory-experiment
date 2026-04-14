import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from 'react';

export interface ChatInputHandle {
  /** Restore text to the input (e.g. after a failed send) and focus */
  setInputText: (text: string) => void;
  focus: () => void;
}

interface ChatInputProps {
  onSend: (content: string) => void;
  isStreaming?: boolean;
  disabled?: boolean;
}

export const ChatInput = forwardRef<ChatInputHandle, ChatInputProps>(
  ({ onSend, isStreaming = false, disabled = false }, ref) => {
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [focused, setFocused] = useState(false);

    const isDisabled = disabled || isStreaming;

    // ── Auto-resize ─────────────────────────────────────────────────
    const adjustHeight = useCallback(() => {
      const el = textareaRef.current;
      if (!el) return;
      el.style.height = 'auto';
      const maxH = 144; // ~6 lines × 24px
      el.style.height = `${Math.min(el.scrollHeight, maxH)}px`;
      el.style.overflowY = el.scrollHeight > maxH ? 'auto' : 'hidden';
    }, []);

    // ── Expose imperative handle ────────────────────────────────────
    useImperativeHandle(ref, () => ({
      setInputText: (text: string) => {
        const el = textareaRef.current;
        if (!el) return;
        el.value = text;
        // Trigger resize after setting value
        setTimeout(adjustHeight, 0);
        el.focus();
      },
      focus: () => textareaRef.current?.focus(),
    }));

    // ── Send ─────────────────────────────────────────────────────────
    const handleSend = useCallback(() => {
      const el = textareaRef.current;
      if (!el) return;
      const content = el.value.trim();
      if (!content || isDisabled) return;

      onSend(content);

      // Reset textarea
      el.value = '';
      el.style.height = 'auto';
      el.style.overflowY = 'hidden';
    }, [onSend, isDisabled]);

    // ── Keyboard ─────────────────────────────────────────────────────
    const handleKeyDown = useCallback(
      (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          handleSend();
        }
        // Shift+Enter: default textarea behavior (inserts newline)
      },
      [handleSend],
    );

    return (
      <div
        style={{
          background: '#111827',
          border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: 12,
          display: 'flex',
          alignItems: 'flex-end',
          gap: 8,
          padding: '10px 12px',
          opacity: isDisabled ? 0.7 : 1,
          transition: 'opacity 0.2s',
          boxShadow: focused && !isDisabled ? '0 0 0 2px #3b82f6' : 'none',
        }}
      >
        {/* ── Textarea ── */}
        <textarea
          ref={textareaRef}
          placeholder={
            isStreaming ? 'Waiting for response…' : 'Ask anything about the video library…'
          }
          disabled={isDisabled}
          onInput={adjustHeight}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          rows={1}
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            color: isDisabled ? '#475569' : '#f1f5f9',
            fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
            fontSize: 15,
            lineHeight: '24px',
            padding: 0,
            resize: 'none',
            outline: 'none',
            overflowY: 'hidden',
            minHeight: 24,
            maxHeight: 144,
            cursor: isDisabled ? 'not-allowed' : 'text',
          }}
        />

        {/* ── Send button ── */}
        <button
          onClick={handleSend}
          disabled={isDisabled}
          aria-label="Send message"
          style={{
            background: isDisabled ? '#1e293b' : '#3b82f6',
            border: 'none',
            borderRadius: 8,
            color: isDisabled ? '#475569' : '#fff',
            cursor: isDisabled ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            height: 34,
            width: 34,
            transition: 'background 0.15s, color 0.15s',
          }}
          onMouseEnter={(e) => {
            if (!isDisabled) e.currentTarget.style.background = '#1d4ed8';
          }}
          onMouseLeave={(e) => {
            if (!isDisabled) e.currentTarget.style.background = isDisabled ? '#1e293b' : '#3b82f6';
          }}
        >
          {isStreaming ? (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle
                cx="8"
                cy="8"
                r="5"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeDasharray="14 6"
                style={{ animation: 'spin 1s linear infinite', transformOrigin: '8px 8px' }}
              />
            </svg>
          ) : (
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="8" y1="14" x2="8" y2="3" />
              <polyline points="3,8 8,3 13,8" />
            </svg>
          )}
        </button>
      </div>
    );
  },
);

ChatInput.displayName = 'ChatInput';

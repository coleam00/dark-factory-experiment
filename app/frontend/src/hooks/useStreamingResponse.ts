import { useCallback, useState } from 'react';
import { RateLimitError } from '../lib/api';

export interface StreamResult {
  fullText: string;
  sources: string[];
}

export function useStreamingResponse() {
  const [streamingContent, setStreamingContent] = useState<string>('');
  const [streamingSources, setStreamingSources] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  const startStream = useCallback(
    async (
      conversationId: string,
      userMessage: string,
      onComplete: (result: StreamResult) => void,
    ): Promise<void> => {
      setIsStreaming(true);
      setStreamingContent('');
      setStreamingSources([]);

      let fullText = '';
      let sources: string[] = [];

      try {
        const res = await fetch(`/api/conversations/${conversationId}/messages`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: userMessage }),
        });

        if (res.status === 401) {
          if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
            const returnTo = window.location.pathname + window.location.search;
            window.location.assign(`/login?from=${encodeURIComponent(returnTo)}`);
          }
          throw new Error('Not authenticated');
        }
        if (res.status === 429) {
          // MISSION §10 #1 — daily cap hit. Body: {error, limit, window_hours, reset_at}.
          const body = await res.json().catch(() => null);
          if (body && typeof body === 'object' && 'limit' in body) {
            throw new RateLimitError(body);
          }
          throw new Error('Daily message limit reached');
        }
        if (!res.ok) {
          const errorText = await res.text().catch(() => '');
          throw new Error(`HTTP ${res.status}${errorText ? `: ${errorText}` : ''}`);
        }
        if (!res.body) throw new Error('No response body');

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        // Buffer for incomplete SSE data between reader.read() calls
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // SSE events are separated by blank lines (\n\n)
          const parts = buffer.split('\n\n');
          // The last part may be incomplete — keep it in the buffer
          buffer = parts.pop() ?? '';

          for (const rawEvent of parts) {
            if (!rawEvent.trim()) continue;

            let eventType = 'message';
            const dataLines: string[] = [];

            for (const line of rawEvent.split('\n')) {
              if (line.startsWith('event:')) {
                eventType = line.slice(6).trim();
              } else if (line.startsWith('data:')) {
                // Support both "data: value" and "data:value"
                const val = line.slice(5);
                dataLines.push(val.startsWith(' ') ? val.slice(1) : val);
              }
            }

            const data = dataLines.join('\n');

            if (eventType === 'sources') {
              // Parse the sources JSON array of video titles
              try {
                const parsed = JSON.parse(data);
                if (Array.isArray(parsed)) {
                  sources = parsed;
                  setStreamingSources(parsed);
                }
              } catch {
                // Ignore malformed sources
              }
            } else if (data === '[DONE]') {
              // Stream complete — no action needed here
            } else if (data.startsWith('{"error"')) {
              // Server sent an error payload mid-stream
              let errMsg = 'Stream error from server';
              try {
                errMsg = JSON.parse(data).error || errMsg;
              } catch {
                // Use default message
              }
              throw new Error(errMsg);
            } else if (data) {
              // Tokens are JSON-encoded strings to safely handle newlines/special chars
              let token = data;
              try {
                const parsed = JSON.parse(data);
                if (typeof parsed === 'string') {
                  token = parsed;
                }
              } catch {
                // Not JSON-encoded — use raw data (backward compat)
              }
              fullText += token;
              setStreamingContent(fullText);
            }
          }
        }

        // Stream completed successfully
        onComplete({ fullText, sources });
      } finally {
        // Always reset streaming state — React 18 batches this with the onComplete
        // state updates, ensuring a seamless transition to the persisted message.
        setIsStreaming(false);
        setStreamingContent('');
        setStreamingSources([]);
      }
    },
    [],
  );

  return { streamingContent, streamingSources, isStreaming, startStream };
}

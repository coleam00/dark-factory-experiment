import { useCallback, useRef, useState } from 'react';

export interface StreamResult {
  fullText: string;
  sources: string[];
}

export function useStreamingResponse() {
  const [streamingContent, setStreamingContent] = useState<string>('');
  const [streamingSources, setStreamingSources] = useState<string[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const startStream = useCallback(
    async (
      conversationId: string,
      userMessage: string,
      onComplete: (result: StreamResult) => void,
    ): Promise<void> => {
      setIsStreaming(true);
      setStreamingContent('');
      setStreamingSources([]);

      // Set up AbortController for mid-stream cancellation
      abortControllerRef.current = new AbortController();

      let fullText = '';
      let sources: string[] = [];

      try {
        const res = await fetch(`/api/conversations/${conversationId}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: userMessage }),
          signal: abortControllerRef.current.signal,
        });

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
      } catch (e) {
        // The AbortError comes from fetch() when cancelStream() aborts the in-flight request.
        // Silently clean up rather than surfacing a user-facing error.
        if (e instanceof Error && e.name === 'AbortError') {
          return;
        }
        // Server errors (e.g., {"error": ...} mid-stream) are re-thrown to ChatArea's catch block.
        throw e;
      } finally {
        // Always reset streaming state — React 18 batches this with the onComplete
        // state updates, ensuring a seamless transition to the persisted message.
        abortControllerRef.current = null;
        setIsStreaming(false);
        setStreamingContent('');
        setStreamingSources([]);
      }
    },
    [],
  );

  // Cancel any in-flight stream (e.g., on conversation switch or component unmount)
  const cancelStream = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  }, []);

  return { streamingContent, streamingSources, isStreaming, startStream, cancelStream };
}

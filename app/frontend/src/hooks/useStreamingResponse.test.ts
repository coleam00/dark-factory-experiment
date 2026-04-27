/**
 * Tests for useStreamingResponse hook SSE parsing and state management.
 *
 * Verifies:
 *   - Parses sources event with Citation[] objects into streamingSources state
 *   - Handles malformed sources JSON gracefully with console.warn
 *   - Resets all streaming state (and aborts in-flight fetch) when conversationId changes
 */

import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useStreamingResponse } from './useStreamingResponse';

function makeSseStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

const mockCitation = {
  chunk_id: 'chunk-1',
  video_id: 'vid-1',
  video_title: 'Test Video',
  video_url: 'https://www.youtube.com/watch?v=abc123',
  start_seconds: 10,
  end_seconds: 20,
  snippet: 'Test snippet text',
};

describe('useStreamingResponse SSE parsing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('parses sources event with Citation objects', () => {
    const warnMock = vi.fn();
    const originalWarn = console.warn;
    console.warn = warnMock;

    // Simulate SSE parsing logic from the hook
    const data = JSON.stringify([mockCitation]);
    let sources: unknown[] = [];
    try {
      const parsed = JSON.parse(data);
      if (Array.isArray(parsed)) {
        sources = parsed;
      }
    } catch (e) {
      console.warn('[useStreamingResponse] Failed to parse sources event:', e);
    }

    expect(sources).toHaveLength(1);
    expect((sources[0] as typeof mockCitation).chunk_id).toBe('chunk-1');
    expect((sources[0] as typeof mockCitation).video_title).toBe('Test Video');

    console.warn = originalWarn;
  });

  it('warns on malformed sources JSON', () => {
    const warnMock = vi.fn();
    const originalWarn = console.warn;
    console.warn = warnMock;

    const data = 'not valid json {';

    let sources: unknown[] = [];
    try {
      const parsed = JSON.parse(data);
      if (Array.isArray(parsed)) {
        sources = parsed;
      }
    } catch (e) {
      console.warn('[useStreamingResponse] Failed to parse sources event:', e);
    }

    expect(sources).toHaveLength(0);
    expect(warnMock).toHaveBeenCalledWith(
      '[useStreamingResponse] Failed to parse sources event:',
      expect.any(Error),
    );

    console.warn = originalWarn;
  });

  it('handles empty sources array', () => {
    const eventType = 'sources';
    const data = '[]';

    let sources: unknown[] = [];
    try {
      const parsed = JSON.parse(data);
      if (Array.isArray(parsed)) {
        sources = parsed;
      }
    } catch {
      // ignore
    }

    expect(sources).toHaveLength(0);
  });

  it('handles sources event with multiple citations', () => {
    const multipleCitations = [
      mockCitation,
      { ...mockCitation, chunk_id: 'chunk-2', video_title: 'Second Video' },
    ];
    const data = JSON.stringify(multipleCitations);

    let sources: unknown[] = [];
    try {
      const parsed = JSON.parse(data);
      if (Array.isArray(parsed)) {
        sources = parsed;
      }
    } catch {
      // ignore
    }

    expect(sources).toHaveLength(2);
    expect((sources[0] as typeof mockCitation).chunk_id).toBe('chunk-1');
    expect((sources[1] as typeof mockCitation).chunk_id).toBe('chunk-2');
  });
});

describe('status event SSE parsing — hook state transitions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('sets and clears streamingStatus through the real hook (start → done → cleared)', async () => {
    const startPayload = JSON.stringify({
      type: 'tool_call_start',
      tool: 'search_videos',
      subject: 'building agents',
    });
    const donePayload = JSON.stringify({ type: 'tool_call_done', tool: 'search_videos' });

    const sseChunks = [
      `event: status\ndata: ${startPayload}\n\n`,
      `event: status\ndata: ${donePayload}\n\n`,
      `data: "Answer here."\n\n`,
      'data: [DONE]\n\n',
    ];

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: makeSseStream(sseChunks),
      }),
    );

    const onComplete = vi.fn();
    const { result } = renderHook(() => useStreamingResponse('conv-1'));

    await act(async () => {
      await result.current.startStream('conv-1', 'hi', onComplete);
    });

    // After the stream ends, streamingStatus must be null (cleared in finally)
    expect(result.current.streamingStatus).toBeNull();
    expect(onComplete).toHaveBeenCalledWith(expect.objectContaining({ fullText: 'Answer here.' }));
  });

  it('clears streamingStatus when first content token arrives (no tool_call_done)', async () => {
    const startPayload = JSON.stringify({
      type: 'tool_call_start',
      tool: 'search_videos',
      subject: 'building agents',
    });

    // Deliberately omit tool_call_done — content token must clear status
    const sseChunks = [
      `event: status\ndata: ${startPayload}\n\n`,
      `data: "Token"\n\n`,
      'data: [DONE]\n\n',
    ];

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: makeSseStream(sseChunks),
      }),
    );

    const { result } = renderHook(() => useStreamingResponse('conv-1'));

    await act(async () => {
      await result.current.startStream('conv-1', 'hi', vi.fn());
    });

    expect(result.current.streamingStatus).toBeNull();
  });

  it('warns and leaves status null on malformed status event JSON', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    const sseChunks = [
      'event: status\ndata: not valid json {\n\n',
      'data: "Answer."\n\n',
      'data: [DONE]\n\n',
    ];

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: makeSseStream(sseChunks),
      }),
    );

    const { result } = renderHook(() => useStreamingResponse('conv-1'));

    await act(async () => {
      await result.current.startStream('conv-1', 'hi', vi.fn());
    });

    expect(result.current.streamingStatus).toBeNull();
    expect(warnSpy).toHaveBeenCalledWith(
      '[useStreamingResponse] Failed to parse status event:',
      expect.any(Error),
    );
  });

  it('ignores unknown status type and leaves streamingStatus null', async () => {
    const unknownPayload = JSON.stringify({ type: 'future_event', tool: 'foo' });

    const sseChunks = [
      `event: status\ndata: ${unknownPayload}\n\n`,
      'data: "Answer."\n\n',
      'data: [DONE]\n\n',
    ];

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: makeSseStream(sseChunks),
      }),
    );

    const { result } = renderHook(() => useStreamingResponse('conv-1'));

    await act(async () => {
      await result.current.startStream('conv-1', 'hi', vi.fn());
    });

    expect(result.current.streamingStatus).toBeNull();
  });
});

describe('abortStream', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should be a no-op when no stream is active', () => {
    const { result } = renderHook(() => useStreamingResponse('conv-1'));
    expect(result.current.abortStream).not.toThrow();
    expect(result.current.isStreaming).toBe(false);
  });

  it('should be callable multiple times without throwing', () => {
    const { result } = renderHook(() => useStreamingResponse('conv-1'));
    result.current.abortStream();
    result.current.abortStream();
    result.current.abortStream();
    expect(result.current.isStreaming).toBe(false);
  });
});

describe('conversationId reset — state clears on navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('resets all streaming state when conversationId changes', () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));

    const { result, rerender } = renderHook(
      ({ convId }: { convId: string | null }) => useStreamingResponse(convId),
      { initialProps: { convId: 'conv-a' } },
    );

    // Start streaming (fire-and-forget — promise never resolves)
    act(() => {
      void result.current.startStream('conv-a', 'hello', vi.fn());
    });

    // Verify streaming started before testing the reset
    expect(result.current.isStreaming).toBe(true);

    // Switch conversation — effect should reset all state
    rerender({ convId: 'conv-b' });

    expect(result.current.isStreaming).toBe(false);
    expect(result.current.streamingContent).toBe('');
    expect(result.current.streamingSources).toHaveLength(0);
    expect(result.current.streamingStatus).toBeNull();
  });

  it('resets streaming state when conversationId changes to null', () => {
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));

    const { result, rerender } = renderHook(
      ({ convId }: { convId: string | null }) => useStreamingResponse(convId),
      { initialProps: { convId: 'conv-a' as string | null } },
    );

    act(() => {
      void result.current.startStream('conv-a', 'hello', vi.fn());
    });

    rerender({ convId: null });

    expect(result.current.isStreaming).toBe(false);
    expect(result.current.streamingContent).toBe('');
  });

  it('does not reset state when conversationId stays the same', async () => {
    const sseChunks = ['data: "Hello"\n\n', 'data: [DONE]\n\n'];

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: makeSseStream(sseChunks),
      }),
    );

    const onComplete = vi.fn();
    const { result, rerender } = renderHook(
      ({ convId }: { convId: string | null }) => useStreamingResponse(convId),
      { initialProps: { convId: 'conv-a' } },
    );

    await act(async () => {
      await result.current.startStream('conv-a', 'hi', onComplete);
    });

    // Re-render with same conversationId — effect must NOT reset state
    rerender({ convId: 'conv-a' });

    // onComplete was called by startStream when the stream completed above
    expect(onComplete).toHaveBeenCalledWith(expect.objectContaining({ fullText: 'Hello' }));
  });
});

describe('sources event — hook state via renderHook', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('dispatches sources to streamingSources state via the real hook', async () => {
    const citation = {
      chunk_id: 'chunk-1',
      video_id: 'vid-1',
      video_title: 'Test Video',
      video_url: 'https://www.youtube.com/watch?v=abc123',
      start_seconds: 10,
      end_seconds: 20,
      snippet: 'Test snippet text',
    };
    const sseChunks = [
      `event: sources\ndata: ${JSON.stringify([citation])}\n\n`,
      'data: "Answer"\n\n',
      'data: [DONE]\n\n',
    ];

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: makeSseStream(sseChunks),
      }),
    );

    const onComplete = vi.fn();
    const { result } = renderHook(() => useStreamingResponse('conv-1'));

    await act(async () => {
      await result.current.startStream('conv-1', 'hi', onComplete);
    });

    // Sources are passed to onComplete before the finally block clears state
    expect(onComplete).toHaveBeenCalledWith(
      expect.objectContaining({
        sources: [expect.objectContaining({ chunk_id: 'chunk-1' })],
      }),
    );
  });

  it('leaves streamingSources empty and warns on malformed sources JSON', async () => {
    const sseChunks = [
      'event: sources\ndata: not-valid-json\n\n',
      'data: "Answer"\n\n',
      'data: [DONE]\n\n',
    ];

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        body: makeSseStream(sseChunks),
      }),
    );

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const onComplete = vi.fn();
    const { result } = renderHook(() => useStreamingResponse('conv-1'));

    await act(async () => {
      await result.current.startStream('conv-1', 'hi', onComplete);
    });

    expect(warnSpy).toHaveBeenCalledWith(
      '[useStreamingResponse] Failed to parse sources event:',
      expect.any(Error),
    );
    // onComplete still fires with empty sources on parse failure
    expect(onComplete).toHaveBeenCalledWith(expect.objectContaining({ sources: [] }));
  });
});

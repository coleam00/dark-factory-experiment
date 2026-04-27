/**
 * Tests for useStreamingResponse hook SSE parsing.
 *
 * Verifies:
 *   - Parses sources event with Citation[] objects into streamingSources state
 *   - Handles malformed sources JSON gracefully with console.warn
 *   - Resets streaming state and aborts in-flight streams on conversation switch
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

describe('useStreamingResponse SSE parsing — hook state transitions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('populates streamingSources from a sources SSE event', async () => {
    const sseChunks = [
      `event: sources\ndata: ${JSON.stringify([mockCitation])}\n\n`,
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

    const onComplete = vi.fn();
    const { result } = renderHook(() => useStreamingResponse('conv-1'));
    await act(async () => {
      await result.current.startStream('conv-1', 'hi', onComplete);
    });

    expect(onComplete).toHaveBeenCalledWith(
      expect.objectContaining({
        sources: expect.arrayContaining([
          expect.objectContaining({ chunk_id: 'chunk-1', video_title: 'Test Video' }),
        ]),
      }),
    );
    // Hook state is cleared in finally after stream ends
    expect(result.current.streamingSources).toEqual([]);
  });

  it('warns on malformed sources JSON and leaves sources empty', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    const sseChunks = [
      'event: sources\ndata: not valid json {\n\n',
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

    expect(result.current.streamingSources).toEqual([]);
    expect(warnSpy).toHaveBeenCalledWith(
      '[useStreamingResponse] Failed to parse sources event:',
      expect.any(Error),
    );

    warnSpy.mockRestore();
  });

  it('handles empty sources array through the hook', async () => {
    const sseChunks = ['event: sources\ndata: []\n\n', 'data: "Answer."\n\n', 'data: [DONE]\n\n'];
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

    expect(result.current.streamingSources).toEqual([]);
  });

  it('handles sources event with multiple citations', async () => {
    const multipleCitations = [
      mockCitation,
      { ...mockCitation, chunk_id: 'chunk-2', video_title: 'Second Video' },
    ];
    const sseChunks = [
      `event: sources\ndata: ${JSON.stringify(multipleCitations)}\n\n`,
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

    const onComplete = vi.fn();
    const { result } = renderHook(() => useStreamingResponse('conv-1'));
    await act(async () => {
      await result.current.startStream('conv-1', 'hi', onComplete);
    });

    expect(onComplete).toHaveBeenCalledWith(
      expect.objectContaining({
        sources: expect.arrayContaining([
          expect.objectContaining({ chunk_id: 'chunk-1' }),
          expect.objectContaining({ chunk_id: 'chunk-2' }),
        ]),
      }),
    );
    // Hook state is cleared in finally after stream ends
    expect(result.current.streamingSources).toEqual([]);
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

describe('conversationId reset', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('aborts in-flight stream and resets state when conversationId changes', async () => {
    const abortSpy = vi.spyOn(AbortController.prototype, 'abort');

    // Create a fetch that never resolves so the stream stays "in-flight"
    let resolveFetch = (_value: Response) => {};
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(fetchPromise));

    const { result, rerender } = renderHook(({ id }) => useStreamingResponse(id), {
      initialProps: { id: 'conv-1' },
    });

    // Start a stream — this should create an AbortController
    act(() => {
      result.current.startStream('conv-1', 'hello', vi.fn());
    });

    // Stream should be active
    expect(result.current.isStreaming).toBe(true);

    // Simulate the user switching conversations
    rerender({ id: 'conv-2' });

    // The abort should have been called
    expect(abortSpy).toHaveBeenCalled();

    // All state should be reset
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.streamingContent).toBe('');
    expect(result.current.streamingSources).toEqual([]);
    expect(result.current.streamingStatus).toBeNull();

    // Clean up: resolve the pending fetch so the test doesn't hang
    resolveFetch(
      new Response(
        new ReadableStream({
          start(c) {
            c.close();
          },
        }),
        { status: 200 },
      ),
    );
    abortSpy.mockRestore();
  });

  it('does not abort when conversationId stays the same', () => {
    const abortSpy = vi.spyOn(AbortController.prototype, 'abort');
    const { rerender } = renderHook(({ id }) => useStreamingResponse(id), {
      initialProps: { id: 'conv-1' },
    });
    rerender({ id: 'conv-1' });
    expect(abortSpy).not.toHaveBeenCalled();
    abortSpy.mockRestore();
  });
});

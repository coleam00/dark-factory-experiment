/**
 * Unit tests for the "Regenerate" affordance on the last assistant message.
 *
 * Covers:
 *  - button renders only on the last assistant message
 *  - click optimistically removes the old assistant message and POSTs to regenerate
 *  - successful stream replaces the old message with the new one + sources
 *  - failed stream restores the old message and surfaces a toast
 *  - button is hidden while a stream is in flight
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import { type Citation, type Message, RateLimitError } from '../lib/api';

// ── Fixtures used by the mocked streaming hook ───────────────────────────────

const newSource: Citation = {
  chunk_id: 'c-new',
  video_id: 'v-new',
  video_title: 'New Video',
  video_url: 'https://youtube.com/watch?v=new',
  start_seconds: 0,
  end_seconds: 10,
  snippet: 'new snippet',
  source_type: 'youtube',
  is_cited: true,
};

const userMsg: Message = {
  id: 'msg-user',
  conversation_id: 'conv-1',
  role: 'user',
  content: 'Original question',
  created_at: new Date().toISOString(),
};

const oldAssistantMsg: Message = {
  id: 'msg-assistant-old',
  conversation_id: 'conv-1',
  role: 'assistant',
  content: 'Old answer',
  created_at: new Date().toISOString(),
  sources: [
    {
      chunk_id: 'c-old',
      video_id: 'v-old',
      video_title: 'Old Video',
      video_url: 'https://youtube.com/watch?v=old',
      start_seconds: 0,
      end_seconds: 5,
      snippet: 'old snippet',
      source_type: 'youtube',
      is_cited: true,
    },
  ],
};

const earlierAssistantMsg: Message = {
  id: 'msg-assistant-earlier',
  conversation_id: 'conv-1',
  role: 'assistant',
  content: 'Earlier answer',
  created_at: new Date().toISOString(),
};

// ── Shared mocks ─────────────────────────────────────────────────────────────

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

let mockMessages: Message[] = [];
const mockSetMessages = vi.fn();

vi.mock('../hooks/useMessages', () => ({
  useMessages: () => ({
    messages: mockMessages,
    setMessages: mockSetMessages,
    loading: false,
    error: null,
    notFound: false,
    conversation: { id: 'conv-1', title: 'Test Chat', created_at: '', updated_at: '' },
  }),
}));

const streamingStateRef = { current: { isStreaming: false, streamingContent: '' } };

function createSSEStream(body: string): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const data = `data: ${JSON.stringify(body)}\n\ndata: [DONE]\n\n`;
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(data));
      controller.close();
    },
  });
}

vi.mock('../hooks/useStreamingResponse', () => ({
  useStreamingResponse: () => ({
    streamingContent: streamingStateRef.current.streamingContent,
    streamingSources: [],
    streamingStatus: null,
    isStreaming: streamingStateRef.current.isStreaming,
    startStream: vi
      .fn()
      .mockImplementation(async (conversationId, _content, onComplete, options) => {
        const url = options?.regenerate
          ? `/api/conversations/${conversationId}/messages/regenerate`
          : `/api/conversations/${conversationId}/messages`;
        const body = options?.regenerate ? '{}' : JSON.stringify({ content: _content });

        const res = await fetch(url, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body,
        });

        if (res.status === 429) {
          let rateBody: Record<string, unknown> = {};
          try {
            rateBody = await res.json();
          } catch {
            // fall back to defaults
          }
          throw new RateLimitError({
            limit: Number(rateBody.limit ?? 25),
            window_hours: Number(rateBody.window_hours ?? 24),
            reset_at: String(rateBody.reset_at ?? new Date().toISOString()),
          });
        }

        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        if (!res.body) throw new Error('No response body');

        onComplete({ fullText: 'Regenerated response', sources: [newSource] });
      }),
    abortStream: vi.fn(),
  }),
}));

const addToastRef = { current: vi.fn() };

vi.mock('../hooks/useToast', () => ({
  useToast: () => ({
    addToast: addToastRef.current,
    removeToast: vi.fn(),
  }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test.com', is_admin: false },
    refresh: vi.fn(),
  }),
}));

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('ChatArea regenerate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    mockMessages = [];
    streamingStateRef.current = { isStreaming: false, streamingContent: '' };
    addToastRef.current = vi.fn();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders the regenerate button only on the last assistant message', () => {
    mockMessages = [userMsg, earlierAssistantMsg, oldAssistantMsg];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    const buttons = screen.getAllByLabelText('Regenerate response');
    expect(buttons).toHaveLength(1);
    // The only regenerate button belongs to the last assistant message
    expect(buttons[0].closest('div[style*="background: rgb(30, 41, 59)"]')?.textContent).toContain(
      'Old answer',
    );
  });

  it('does not render regenerate on user messages or when streaming', () => {
    streamingStateRef.current = { isStreaming: true, streamingContent: '' };
    mockMessages = [userMsg, oldAssistantMsg];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    expect(screen.queryByLabelText('Regenerate response')).not.toBeInTheDocument();
  });

  it('clicking regenerate removes the old assistant message and POSTs to the regenerate endpoint', async () => {
    mockMessages = [userMsg, oldAssistantMsg];

    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: createSSEStream('Regenerated response'),
    } as unknown as Response);

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    const regenerateButton = screen.getByLabelText('Regenerate response');
    fireEvent.click(regenerateButton);

    await waitFor(() => {
      expect(mockSetMessages).toHaveBeenCalled();
    });

    // First call removes the last message
    const firstCall = mockSetMessages.mock.calls[0][0];
    expect(typeof firstCall).toBe('function');
    expect(firstCall([userMsg, oldAssistantMsg])).toEqual([userMsg]);

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/conversations/conv-1/messages/regenerate',
        expect.objectContaining({
          method: 'POST',
          body: '{}',
        }),
      );
    });
  });

  it('replaces the old assistant message with the streamed response and new sources', async () => {
    mockMessages = [userMsg, oldAssistantMsg];

    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: createSSEStream('Regenerated response'),
    } as unknown as Response);

    const { rerender } = render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('Regenerate response'));

    await waitFor(() => {
      expect(mockSetMessages).toHaveBeenCalledTimes(2);
    });

    // Simulate the optimistic removal
    mockMessages = [userMsg];
    rerender(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );
    expect(screen.queryByText('Old answer')).not.toBeInTheDocument();

    // Simulate the onComplete append
    const secondCall = mockSetMessages.mock.calls[1][0];
    expect(typeof secondCall).toBe('function');
    mockMessages = secondCall([userMsg]);
    rerender(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    expect(screen.getByText('Regenerated response')).toBeInTheDocument();
    expect(screen.getByText(/New Video/i)).toBeInTheDocument();
    expect(screen.queryByText('Old answer')).not.toBeInTheDocument();
  });

  it('restores the old assistant message and toasts on failure', async () => {
    mockMessages = [userMsg, oldAssistantMsg];

    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network error'));

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('Regenerate response'));

    await waitFor(() => {
      expect(mockSetMessages).toHaveBeenCalledTimes(2);
    });

    // First call: remove the old assistant
    const firstCall = mockSetMessages.mock.calls[0][0];
    expect(firstCall([userMsg, oldAssistantMsg])).toEqual([userMsg]);

    // Second call: restore the removed assistant
    const secondCall = mockSetMessages.mock.calls[1][0];
    expect(secondCall([userMsg])).toEqual([userMsg, oldAssistantMsg]);

    expect(addToastRef.current).toHaveBeenCalledWith(
      'Failed to regenerate response. Please try again.',
      'error',
    );
  });

  it('restores the old message and shows inline rate-limit text when the daily cap is hit', async () => {
    mockMessages = [userMsg, oldAssistantMsg];

    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({
        error: 'rate_limit_exceeded',
        limit: 25,
        window_hours: 24,
        reset_at: new Date(Date.now() + 3600_000).toISOString(),
      }),
    } as unknown as Response);

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByLabelText('Regenerate response'));

    await waitFor(() => {
      expect(mockSetMessages).toHaveBeenCalledTimes(2);
    });

    const restoreFn = mockSetMessages.mock.calls[1][0];
    expect(restoreFn([userMsg])).toEqual([userMsg, oldAssistantMsg]);

    expect(addToastRef.current).toHaveBeenCalledWith(
      expect.stringContaining('daily message limit'),
      'error',
    );
  });
});

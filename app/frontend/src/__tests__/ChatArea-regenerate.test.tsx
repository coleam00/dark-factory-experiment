/**
 * Behavior tests for the Regenerate flow in ChatArea (issue #280).
 *
 * Uses the REAL useStreamingResponse hook with a stubbed global fetch so the
 * test exercises the full path: click Regenerate → optimistic removal of the
 * old assistant bubble → POST /messages/regenerate → SSE stream → streamed
 * replacement appended. The useMessages mock is backed by real React state
 * so setMessages updates are observable in the DOM.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import type { Message as MessageType } from '../lib/api';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mutable refs captured by the hoisted mock factories — reset in beforeEach.
const initialMessagesRef: { current: MessageType[] } = { current: [] };
const addToastRef = { current: vi.fn() };
const refreshAuthRef = { current: vi.fn() };

// Stateful useMessages mock: real useState so ChatArea's setMessages calls
// re-render the component and the optimistic removal/append is observable.
vi.mock('../hooks/useMessages', async () => {
  const { useState } = await import('react');
  return {
    useMessages: () => {
      const [messages, setMessages] = useState<MessageType[]>(initialMessagesRef.current);
      return {
        messages,
        setMessages,
        loading: false,
        error: null,
        notFound: false,
        conversation: { id: 'conv-1', title: 'Test', created_at: '', updated_at: '' },
      };
    },
  };
});

vi.mock('../hooks/useToast', () => ({
  useToast: () => ({
    addToast: addToastRef.current,
    removeToast: vi.fn(),
  }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test', is_admin: false },
    refresh: refreshAuthRef.current,
  }),
}));

function makeSseStream(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

function conversationEndingInAssistant(): MessageType[] {
  return [
    {
      id: 'u1',
      conversation_id: 'conv-1',
      role: 'user',
      content: 'What is RAG?',
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 'a1',
      conversation_id: 'conv-1',
      role: 'assistant',
      content: 'Old answer.',
      created_at: '2026-01-01T00:00:01Z',
    },
  ];
}

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  initialMessagesRef.current = conversationEndingInAssistant();
  addToastRef.current = vi.fn();
  refreshAuthRef.current = vi.fn();
});

describe('ChatArea — Regenerate flow (issue #280)', () => {
  it('removes the old assistant bubble and appends the streamed replacement', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: makeSseStream(['data: "New answer."\n\n', 'data: [DONE]\n\n']),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    expect(screen.getByText('Old answer.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /regenerate/i }));

    await waitFor(() => {
      expect(screen.getByText('New answer.')).toBeInTheDocument();
    });
    // The old answer was replaced, not kept alongside the new one.
    expect(screen.queryByText('Old answer.')).not.toBeInTheDocument();

    // The request went to the regenerate endpoint with no JSON body.
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/conversations/conv-1/messages/regenerate');
    expect(init.body).toBeUndefined();

    // Quota counter refreshed — regeneration counts against usage.
    await waitFor(() => {
      expect(refreshAuthRef.current).toHaveBeenCalled();
    });
  });

  it('restores the old answer and shows the friendly message on RateLimitError', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({
        error: 'rate_limit_exceeded',
        limit: 25,
        window_hours: 24,
        reset_at: '2026-01-02T12:00:00Z',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: /regenerate/i }));

    // The old answer must come back — unlike a normal send, it was removed
    // optimistically before the request failed.
    await waitFor(() => {
      expect(screen.getByText('Old answer.')).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(addToastRef.current).toHaveBeenCalledWith(
        expect.stringContaining("You've hit your daily message limit (25/day)"),
        'error',
      );
    });
  });

  it('does not offer Regenerate when the conversation ends with a user message', () => {
    initialMessagesRef.current = [
      {
        id: 'u1',
        conversation_id: 'conv-1',
        role: 'user',
        content: 'Unanswered question',
        created_at: '2026-01-01T00:00:00Z',
      },
    ];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('button', { name: /regenerate/i })).not.toBeInTheDocument();
  });
});

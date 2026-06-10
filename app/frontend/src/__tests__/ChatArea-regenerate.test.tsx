/**
 * Integration tests for the regenerate flow in ChatArea (issue #280).
 *
 * Verifies:
 *  - The Regenerate button appears only on the LAST assistant message.
 *  - Clicking it removes the stale assistant message and streams a replacement
 *    that lands with its own sources.
 *  - A RateLimitError restores the original message and surfaces the limit error.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import { RateLimitError, type Message as MessageType } from '../lib/api';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

// ── Controllable message state shared with the useMessages mock ──
const INITIAL_MESSAGES: MessageType[] = [
  {
    id: 'u1',
    conversation_id: 'conv-1',
    role: 'user',
    content: 'What is hybrid RAG?',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'a1',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: 'Old answer.',
    created_at: '2026-01-01T00:00:01Z',
    sources: [
      {
        chunk_id: 'old-c',
        video_id: 'v0',
        video_title: 'Old Video',
        video_url: 'https://youtube.com/watch?v=old',
        start_seconds: 1,
        end_seconds: 2,
        snippet: 'old snippet',
      },
    ],
  },
];

let initialMessagesForRender: MessageType[] = INITIAL_MESSAGES;

vi.mock('../hooks/useMessages', () => ({
  useMessages: () => {
    const [messages, setMessages] = useState<MessageType[]>(initialMessagesForRender);
    return {
      messages,
      setMessages,
      loading: false,
      error: null,
      notFound: false,
      conversation: { id: 'conv-1', title: 'Test', created_at: '', updated_at: '' },
    };
  },
}));

const regenerateStreamMock = { current: vi.fn() };

vi.mock('../hooks/useStreamingResponse', () => ({
  useStreamingResponse: () => ({
    streamingContent: '',
    streamingSources: [],
    streamingStatus: null,
    isStreaming: false,
    startStream: vi.fn(),
    regenerateStream: (...args: unknown[]) => regenerateStreamMock.current(...args),
    abortStream: vi.fn(),
  }),
}));

const addToastMock = { current: vi.fn() };
vi.mock('../hooks/useToast', () => ({
  useToast: () => ({ addToast: addToastMock.current, removeToast: vi.fn() }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u', email: 'e', is_admin: false }, refresh: vi.fn() }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  initialMessagesForRender = INITIAL_MESSAGES;
  addToastMock.current = vi.fn();
  regenerateStreamMock.current = vi.fn();
});

function renderChatArea() {
  return render(
    <MemoryRouter>
      <ChatArea conversationId="conv-1" />
    </MemoryRouter>,
  );
}

describe('ChatArea regenerate (issue #280)', () => {
  it('shows the regenerate button only on the last assistant message', async () => {
    renderChatArea();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Regenerate response' })).toBeInTheDocument();
    });
    // Only one regenerate button (the single trailing assistant message).
    expect(screen.getAllByRole('button', { name: 'Regenerate response' })).toHaveLength(1);
  });

  it('removes the stale answer and streams a replacement with new sources', async () => {
    regenerateStreamMock.current = vi
      .fn()
      .mockImplementation(async (_convId: string, onComplete: (r: unknown) => void) => {
        onComplete({
          fullText: 'Fresh answer.',
          sources: [
            {
              chunk_id: 'new-c',
              video_id: 'v1',
              video_title: 'New Video',
              video_url: 'https://youtube.com/watch?v=new',
              start_seconds: 5,
              end_seconds: 6,
              snippet: 'new snippet',
            },
          ],
        });
      });

    renderChatArea();

    const btn = await screen.findByRole('button', { name: 'Regenerate response' });
    fireEvent.click(btn);

    await waitFor(() => {
      expect(screen.getByText('Fresh answer.')).toBeInTheDocument();
    });
    // Old answer is gone, replaced by the fresh one.
    expect(screen.queryByText('Old answer.')).not.toBeInTheDocument();
    expect(regenerateStreamMock.current).toHaveBeenCalledTimes(1);
    expect(regenerateStreamMock.current.mock.calls[0][0]).toBe('conv-1');
  });

  it('restores the original answer and shows the limit error on RateLimitError', async () => {
    regenerateStreamMock.current = vi.fn().mockImplementation(async () => {
      throw new RateLimitError({
        limit: 25,
        window_hours: 24,
        reset_at: '2026-01-02T00:00:00Z',
      });
    });

    renderChatArea();

    const btn = await screen.findByRole('button', { name: 'Regenerate response' });
    fireEvent.click(btn);

    // Original answer restored after the rejection.
    await waitFor(() => {
      expect(screen.getByText('Old answer.')).toBeInTheDocument();
    });
    expect(addToastMock.current).toHaveBeenCalledWith(
      expect.stringContaining('daily message limit'),
      'error',
    );
  });
});

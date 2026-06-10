/**
 * Integration tests for the regenerate flow in ChatArea (issue #280).
 *
 * Verifies:
 *   - The regenerate button renders only on the last assistant message
 *   - Clicking it removes the old answer and shows the streaming bubble
 *   - On 429 the original answer is restored and the rate-limit inline
 *     error is shown (the server rejects before deleting anything)
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import { type Message as MessageType, RateLimitError } from '../lib/api';

type RegenOnComplete = (result: { fullText: string; sources: unknown[] }) => void;

const ctl = vi.hoisted(() => ({
  initialMessages: [] as unknown[],
  regenImpl: null as null | ((convId: string, onComplete: RegenOnComplete) => Promise<void>),
  addToast: vi.fn(),
  refreshAuth: vi.fn(),
}));

vi.mock('../hooks/useMessages', async () => {
  const { useState } = await import('react');
  return {
    useMessages: () => {
      const [messages, setMessages] = useState(ctl.initialMessages);
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

vi.mock('../hooks/useStreamingResponse', async () => {
  const { useState } = await import('react');
  return {
    useStreamingResponse: () => {
      const [isStreaming, setIsStreaming] = useState(false);
      const startRegenerate = async (convId: string, onComplete: RegenOnComplete) => {
        setIsStreaming(true);
        try {
          if (ctl.regenImpl) await ctl.regenImpl(convId, onComplete);
        } finally {
          setIsStreaming(false);
        }
      };
      return {
        streamingContent: '',
        streamingSources: [],
        streamingStatus: null,
        isStreaming,
        startStream: vi.fn(),
        startRegenerate,
        abortStream: vi.fn(),
      };
    },
  };
});

vi.mock('../hooks/useToast', () => ({
  useToast: () => ({ addToast: ctl.addToast, removeToast: vi.fn() }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test', is_admin: false },
    refresh: ctl.refreshAuth,
  }),
}));

function makeMessages(): MessageType[] {
  return [
    {
      id: 'm1',
      conversation_id: 'conv-1',
      role: 'user',
      content: 'First question',
      created_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 'm2',
      conversation_id: 'conv-1',
      role: 'assistant',
      content: 'First answer',
      created_at: '2026-01-01T00:00:01Z',
    },
    {
      id: 'm3',
      conversation_id: 'conv-1',
      role: 'user',
      content: 'Second question',
      created_at: '2026-01-01T00:00:02Z',
    },
    {
      id: 'm4',
      conversation_id: 'conv-1',
      role: 'assistant',
      content: 'Second answer',
      created_at: '2026-01-01T00:00:03Z',
    },
  ];
}

function renderChatArea() {
  return render(
    <MemoryRouter>
      <ChatArea conversationId="conv-1" />
    </MemoryRouter>,
  );
}

describe('ChatArea — regenerate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    ctl.initialMessages = makeMessages();
    ctl.regenImpl = null;
    ctl.addToast = vi.fn();
    ctl.refreshAuth = vi.fn();
  });

  it('renders the regenerate button only on the last assistant message', () => {
    renderChatArea();

    // Two assistant messages are rendered, but only the final one gets the prop.
    expect(screen.getByText('First answer')).toBeInTheDocument();
    expect(screen.getByText('Second answer')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Regenerate response' })).toHaveLength(1);
  });

  it('removes the last answer and shows the streaming bubble on click', async () => {
    // Never-resolving stream keeps isStreaming=true after the click.
    ctl.regenImpl = () => new Promise(() => {});

    renderChatArea();

    fireEvent.click(screen.getByRole('button', { name: 'Regenerate response' }));

    await waitFor(() => {
      expect(screen.queryByText('Second answer')).not.toBeInTheDocument();
    });
    // Streaming bubble with empty content renders the typing indicator.
    await waitFor(() => {
      expect(document.querySelectorAll('.typing-dot')).toHaveLength(3);
    });
    // The button is hidden while the replacement streams.
    expect(screen.queryByRole('button', { name: 'Regenerate response' })).not.toBeInTheDocument();
  });

  it('appends the fresh answer when the stream completes', async () => {
    ctl.regenImpl = async (_convId, onComplete) => {
      onComplete({ fullText: 'Regenerated answer', sources: [] });
    };

    renderChatArea();

    fireEvent.click(screen.getByRole('button', { name: 'Regenerate response' }));

    await waitFor(() => {
      expect(screen.getByText('Regenerated answer')).toBeInTheDocument();
    });
    expect(screen.queryByText('Second answer')).not.toBeInTheDocument();
    // Quota counter refreshed after the regenerate spent a message.
    expect(ctl.refreshAuth).toHaveBeenCalled();
  });

  it('restores the original answer and shows the rate-limit error on 429', async () => {
    ctl.regenImpl = async () => {
      throw new RateLimitError({
        limit: 25,
        window_hours: 24,
        reset_at: '2026-01-02T00:00:00Z',
      });
    };

    renderChatArea();

    fireEvent.click(screen.getByRole('button', { name: 'Regenerate response' }));

    // The 429 arrives before the server deletes anything — the original
    // answer must come back.
    await waitFor(() => {
      expect(screen.getByText('Second answer')).toBeInTheDocument();
    });
    expect(screen.getByText(/daily message limit \(25\/day\)/)).toBeInTheDocument();
    expect(ctl.addToast).toHaveBeenCalledWith(
      expect.stringMatching(/daily message limit/),
      'error',
    );
    expect(ctl.refreshAuth).toHaveBeenCalled();
  });

  it('restores the original answer and shows a generic error on failure', async () => {
    ctl.regenImpl = async () => {
      throw new Error('HTTP 500');
    };

    renderChatArea();

    fireEvent.click(screen.getByRole('button', { name: 'Regenerate response' }));

    await waitFor(() => {
      expect(screen.getByText('Second answer')).toBeInTheDocument();
    });
    expect(
      screen.getByText('Failed to regenerate the response. Please try again.'),
    ).toBeInTheDocument();
    expect(ctl.addToast).toHaveBeenCalledWith('HTTP 500', 'error');
  });
});

/**
 * Tests for ChatArea regenerate orchestration (issue #280).
 *
 * Covers:
 *  - the Regenerate action renders only on the most recent assistant message,
 *  - clicking it calls startRegenerate and optimistically removes the old answer,
 *  - the completion callback appends the fresh assistant message,
 *  - the button is hidden while a stream is in flight,
 *  - a failed regenerate restores the previous answer and surfaces an error.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import type { Message } from '../lib/api';

// ── Shared mocks ──────────────────────────────────────────────────────────────

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

let mockMessages: Message[] = [];
const setMessagesSpy = vi.fn();

vi.mock('../hooks/useMessages', () => ({
  useMessages: () => ({
    messages: mockMessages,
    setMessages: setMessagesSpy,
    loading: false,
    error: null,
    notFound: false,
    conversation: { id: 'conv-1', title: 'Test Chat', created_at: '', updated_at: '' },
  }),
}));

let mockIsStreaming = false;
const startRegenerateSpy = vi.fn();

vi.mock('../hooks/useStreamingResponse', () => ({
  useStreamingResponse: () => ({
    streamingContent: '',
    streamingSources: [],
    streamingStatus: null,
    isStreaming: mockIsStreaming,
    startStream: vi.fn(),
    startRegenerate: startRegenerateSpy,
    abortStream: vi.fn(),
  }),
}));

const addToastSpy = vi.fn();
vi.mock('../hooks/useToast', () => ({
  useToast: () => ({ addToast: addToastSpy, removeToast: vi.fn() }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test.com', is_admin: false },
    refresh: vi.fn(),
  }),
}));

// ── Fixtures ──────────────────────────────────────────────────────────────────

function userMsg(id: string, content: string): Message {
  return {
    id,
    conversation_id: 'conv-1',
    role: 'user',
    content,
    created_at: new Date().toISOString(),
  };
}

function assistantMsg(id: string, content: string): Message {
  return {
    id,
    conversation_id: 'conv-1',
    role: 'assistant',
    content,
    created_at: new Date().toISOString(),
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('ChatArea — regenerate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    mockIsStreaming = false;
    startRegenerateSpy.mockReset();
  });

  afterEach(() => {
    mockMessages = [];
  });

  const regenName = { name: /regenerate response/i };

  it('renders the Regenerate action only on the most recent assistant message', () => {
    mockMessages = [
      userMsg('u1', 'first question'),
      assistantMsg('a1', 'first answer'),
      userMsg('u2', 'second question'),
      assistantMsg('a2', 'second answer'),
    ];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    // Exactly one regenerate button across the whole transcript.
    expect(screen.getAllByRole('button', regenName)).toHaveLength(1);
  });

  it('hides the Regenerate action while a stream is in flight', () => {
    mockIsStreaming = true;
    mockMessages = [userMsg('u1', 'q'), assistantMsg('a1', 'a')];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    expect(screen.queryByRole('button', regenName)).not.toBeInTheDocument();
  });

  it('removes the stale answer and appends the fresh one on success', async () => {
    startRegenerateSpy.mockResolvedValue(undefined);
    mockMessages = [userMsg('u1', 'q'), assistantMsg('a1', 'stale answer')];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', regenName));

    // startRegenerate called with the conversation id + a completion callback.
    expect(startRegenerateSpy).toHaveBeenCalledTimes(1);
    expect(startRegenerateSpy.mock.calls[0][0]).toBe('conv-1');
    const onComplete = startRegenerateSpy.mock.calls[0][1] as (r: {
      fullText: string;
      sources: never[];
    }) => void;

    // First setMessages call optimistically drops the stale assistant answer.
    const removeUpdater = setMessagesSpy.mock.calls[0][0] as (prev: Message[]) => Message[];
    expect(removeUpdater(mockMessages).map((m) => m.id)).toEqual(['u1']);

    // Drive the completion callback → the fresh answer is appended.
    setMessagesSpy.mockClear();
    onComplete({ fullText: 'fresh answer', sources: [] });
    const appendUpdater = setMessagesSpy.mock.calls[0][0] as (prev: Message[]) => Message[];
    const appended = appendUpdater([userMsg('u1', 'q')]);
    expect(appended).toHaveLength(2);
    expect(appended[1]).toMatchObject({ role: 'assistant', content: 'fresh answer' });
  });

  it('restores the previous answer and shows an error when regenerate fails', async () => {
    startRegenerateSpy.mockRejectedValue(new Error('network boom'));
    const previous = assistantMsg('a1', 'previous answer');
    mockMessages = [userMsg('u1', 'q'), previous];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', regenName));

    // The inline error renders after the rejected promise settles.
    await waitFor(() => {
      expect(screen.getByText(/failed to regenerate/i)).toBeInTheDocument();
    });

    // A restore updater re-adds the previous answer when it's no longer present.
    const restoreCall = setMessagesSpy.mock.calls.find((call) => {
      const updater = call[0] as (prev: Message[]) => Message[];
      return updater([userMsg('u1', 'q')]).some((m) => m.id === 'a1');
    });
    expect(restoreCall).toBeDefined();
  });
});

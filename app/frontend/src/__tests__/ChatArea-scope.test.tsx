/**
 * Unit tests for the conversation video-scope UI in ChatArea (issue #279).
 *
 * - The Scope button reflects the conversation's current scope ("Scope: All"
 *   vs "Scope: N").
 * - Opening the picker, selecting videos, and saving calls
 *   updateConversationScope with the chosen ids.
 * - Clearing the selection saves `null` (search everything).
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import type { Conversation, Video } from '../lib/api';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// Mutable conversation so each test can set its starting scope.
let mockConversation: Conversation = {
  id: 'conv-1',
  title: 'Test Chat',
  created_at: '',
  updated_at: '',
  scoped_video_ids: null,
};
const setConversation = vi.fn();

vi.mock('../hooks/useMessages', () => ({
  useMessages: () => ({
    messages: [],
    setMessages: vi.fn(),
    loading: false,
    error: null,
    notFound: false,
    conversation: mockConversation,
    setConversation,
  }),
}));

vi.mock('../hooks/useStreamingResponse', () => ({
  useStreamingResponse: () => ({
    streamingContent: '',
    streamingSources: [],
    streamingStatus: undefined,
    isStreaming: false,
    startStream: vi.fn(),
    abortStream: vi.fn(),
  }),
}));

const addToast = vi.fn();
vi.mock('../hooks/useToast', () => ({
  useToast: () => ({ addToast, removeToast: vi.fn() }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test.com', is_admin: false },
    refresh: vi.fn(),
  }),
}));

const videos: Video[] = [
  { id: 'v1', title: 'Subagents 101', description: '', url: '', created_at: '' },
  { id: 'v2', title: 'Agent Teams', description: '', url: '', created_at: '' },
];

const getVideos = vi.fn(async () => videos);
const updateConversationScope = vi.fn(async (_id: string, ids: string[] | null) => ({
  ...mockConversation,
  scoped_video_ids: ids,
}));

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    getVideos: (...args: unknown[]) => getVideos(...(args as [])),
    updateConversationScope: (id: string, ids: string[] | null) =>
      updateConversationScope(id, ids),
  };
});

describe('ChatArea conversation scope', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    mockConversation = {
      id: 'conv-1',
      title: 'Test Chat',
      created_at: '',
      updated_at: '',
      scoped_video_ids: null,
    };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows "Scope: All" when no scope is set', () => {
    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );
    expect(screen.getByText('Scope: All')).toBeInTheDocument();
  });

  it('shows the scoped count when a scope is set', () => {
    mockConversation.scoped_video_ids = ['v1', 'v2'];
    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );
    expect(screen.getByText('Scope: 2')).toBeInTheDocument();
  });

  it('opens the picker, selects a video, and saves the scope', async () => {
    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByText('Scope: All'));

    // Modal loads videos
    await waitFor(() => {
      expect(screen.getByText('Subagents 101')).toBeInTheDocument();
    });

    // Check the first video then save.
    fireEvent.click(screen.getByText('Subagents 101'));
    fireEvent.click(screen.getByText('Save scope'));

    await waitFor(() => {
      expect(updateConversationScope).toHaveBeenCalledWith('conv-1', ['v1']);
    });
  });

  it('saves null when the user clears the selection', async () => {
    mockConversation.scoped_video_ids = ['v1'];
    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByText('Scope: 1'));

    await waitFor(() => {
      expect(screen.getByText('Subagents 101')).toBeInTheDocument();
    });

    // Pre-checked v1 → clear scope → save.
    fireEvent.click(screen.getByText('Clear scope'));
    fireEvent.click(screen.getByText('Save scope'));

    await waitFor(() => {
      expect(updateConversationScope).toHaveBeenCalledWith('conv-1', null);
    });
  });
});

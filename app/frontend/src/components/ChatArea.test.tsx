import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import * as api from '../lib/api';
import { ChatArea } from './ChatArea';

const { navigateMock } = vi.hoisted(() => {
  return { navigateMock: vi.fn() };
});

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useLocation: () => ({ state: null }),
  };
});

vi.mock('../hooks/useAuth', () => ({
  useAuth: vi.fn(() => ({
    refresh: vi.fn(),
    user: null,
  })),
}));

vi.mock('../hooks/useMessages', () => ({
  useMessages: vi.fn(() => ({
    messages: [],
    setMessages: vi.fn(),
    loading: false,
    error: null,
    notFound: false,
    conversation: null,
  })),
}));

vi.mock('../hooks/useStreamingResponse', () => ({
  useStreamingResponse: vi.fn(() => ({
    streamingContent: '',
    streamingSources: [],
    streamingStatus: null,
    isStreaming: false,
    startStream: vi.fn(),
    abortStream: vi.fn(),
  })),
}));

vi.mock('../hooks/useToast', () => ({
  useToast: vi.fn(() => ({
    addToast: vi.fn(),
  })),
}));

describe('ChatArea scope picker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigateMock.mockReset();
  });

  it('renders the scope picker when starting a new conversation', () => {
    render(
      <MemoryRouter>
        <ChatArea />
      </MemoryRouter>
    );
    expect(screen.getByText('Scope to specific videos')).toBeInTheDocument();
  });

  it('hides the picker and shows a scope indicator once a scoped conversation is active', () => {
    const { useMessages } = require('../hooks/useMessages');
    vi.mocked(useMessages).mockReturnValue({
      messages: [],
      setMessages: vi.fn(),
      loading: false,
      error: null,
      notFound: false,
      conversation: {
        id: 'c1',
        title: 'Test',
        created_at: '2024-01-01',
        updated_at: '2024-01-01',
        scoped_video_ids: ['v1', 'v2'],
      },
    });

    render(
      <MemoryRouter>
        <ChatArea conversationId="c1" />
      </MemoryRouter>
    );

    expect(screen.queryByText('Scope to specific videos')).not.toBeInTheDocument();
    expect(screen.getByText('Scoped to 2 videos')).toBeInTheDocument();
  });

  it('createConversation API call includes scoped_video_ids when provided', async () => {
    vi.spyOn(api, 'getVideos').mockResolvedValue([
      { id: 'v1', title: 'Video 1', description: '', url: '', created_at: '' },
      { id: 'v2', title: 'Video 2', description: '', url: '', created_at: '' },
    ]);

    const createConversationSpy = vi.spyOn(api, 'createConversation').mockResolvedValue({
      id: 'new-conv',
      title: 'New Conversation',
      created_at: '2024-01-01',
      updated_at: '2024-01-01',
    });

    render(
      <MemoryRouter>
        <ChatArea />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByText('Scope to specific videos'));
    await waitFor(() => expect(screen.getByText('Video 1')).toBeInTheDocument());

    const checkbox = screen.getByText('Video 1').closest('label')!.querySelector('input')!;
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByText('Done'));

    const input = screen.getByPlaceholderText('Ask anything about the video library…');
    fireEvent.change(input, { target: { value: 'hello' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(createConversationSpy).toHaveBeenCalledWith(['v1']);
    });
  });
});

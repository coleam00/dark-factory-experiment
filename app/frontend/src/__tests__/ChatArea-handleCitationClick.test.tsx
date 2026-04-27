/**
 * Unit tests for ChatArea.handleCitationClick branching logic (issue #216 fix).
 *
 * Dynamous citations → window.open(lesson_url, '_blank', 'noopener,noreferrer')
 * YouTube citations  → opens CitationModal (setSelectedCitation)
 * Missing lesson_url → does nothing (no blank tab)
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import type { Citation, Message } from '../lib/api';

// ── Shared mocks ──────────────────────────────────────────────────────────────

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

// Module-level storage so we can swap messages between tests
let mockMessages: Message[] = [];

vi.mock('../hooks/useMessages', () => ({
  useMessages: () => ({
    messages: mockMessages,
    setMessages: vi.fn(),
    loading: false,
    error: null,
    notFound: false,
    conversation: { id: 'conv-1', title: 'Test Chat', created_at: '', updated_at: '' },
  }),
}));

vi.mock('../hooks/useStreamingResponse', () => ({
  useStreamingResponse: () => ({
    streamingContent: '',
    streamingSources: [],
    isStreaming: false,
    startStream: vi.fn(),
    abortStream: vi.fn(),
  }),
}));

vi.mock('../hooks/useToast', () => ({
  useToast: () => ({ addToast: vi.fn(), removeToast: vi.fn() }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test.com', is_admin: false },
    refresh: vi.fn(),
  }),
}));

// ── Citation fixtures ─────────────────────────────────────────────────────────

const dynaCitation: Citation = {
  chunk_id: 'c1',
  video_id: 'v1',
  video_title: 'Lesson One',
  video_url: 'https://community.dynamous.ai/c/module/v1',
  start_seconds: 0,
  end_seconds: 10,
  snippet: '',
  source_type: 'dynamous',
  lesson_url: 'https://community.dynamous.ai/c/module-1/lessons/42',
  is_cited: true,
};

const dynaCitationNoUrl: Citation = {
  chunk_id: 'c2',
  video_id: 'v2',
  video_title: 'Lesson Two',
  video_url: 'https://community.dynamous.ai/c/module/v2',
  start_seconds: 0,
  end_seconds: 5,
  snippet: '',
  source_type: 'dynamous',
  lesson_url: undefined,
  is_cited: true,
};

const ytCitation: Citation = {
  chunk_id: 'c3',
  video_id: 'dQw4w9WgXcQ',
  video_title: 'YouTube Video Title',
  video_url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
  start_seconds: 60,
  end_seconds: 70,
  snippet: 'A snippet of text.',
  source_type: 'youtube',
  is_cited: true,
};

function makeAssistantMessage(citation: Citation): Message {
  return {
    id: 'msg-1',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: 'Here is a source.',
    created_at: new Date().toISOString(),
    sources: [citation],
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('handleCitationClick', () => {
  let openSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    Element.prototype.scrollIntoView = vi.fn();
    openSpy = vi.fn();
    vi.stubGlobal('open', openSpy);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    mockMessages = [];
  });

  it('opens lesson_url in a new tab for Dynamous citations', async () => {
    mockMessages = [makeAssistantMessage(dynaCitation)];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    // Wait for the citation chip to render
    await waitFor(() => {
      expect(screen.getByText(/Lesson One/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Lesson One/i));

    expect(openSpy).toHaveBeenCalledWith(
      'https://community.dynamous.ai/c/module-1/lessons/42',
      '_blank',
      'noopener,noreferrer',
    );
    // CitationModal must NOT appear
    expect(screen.queryByTitle('YouTube video player')).not.toBeInTheDocument();
  });

  it('does nothing when Dynamous citation has no lesson_url', async () => {
    mockMessages = [makeAssistantMessage(dynaCitationNoUrl)];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText(/Lesson Two/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/Lesson Two/i));

    // window.open must NOT be called — no blank tab opened
    expect(openSpy).not.toHaveBeenCalled();
    expect(screen.queryByTitle('YouTube video player')).not.toBeInTheDocument();
  });

  it('opens the citation modal for YouTube citations', async () => {
    mockMessages = [makeAssistantMessage(ytCitation)];

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText(/YouTube Video Title/i)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText(/YouTube Video Title/i));

    // window.open must NOT be called
    expect(openSpy).not.toHaveBeenCalled();
    // CitationModal with YouTube iframe must appear
    await waitFor(() => {
      expect(screen.getByTitle('YouTube video player')).toBeInTheDocument();
    });
  });
});

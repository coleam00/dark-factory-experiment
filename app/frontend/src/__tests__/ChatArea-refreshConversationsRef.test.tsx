/**
 * Integration test for refreshConversationsRef cross-component refetch pattern.
 *
 * Tests that after sending a message, the refreshConversationsRef is called
 * to trigger a sidebar conversation list refetch (issue #77 fix).
 *
 * The pattern:
 * - App.tsx creates a conversationsRef and passes it to Sidebar and ChatArea
 * - Sidebar.tsx stores its refetch function in conversationsRef.current
 * - ChatArea.tsx calls conversationsRef.current?.() after a message is sent
 *
 * This test verifies the wiring in ChatArea works correctly.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import * as api from '../lib/api';

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock the hooks that ChatArea depends on
vi.mock('../hooks/useMessages', () => ({
  useMessages: () => ({
    messages: [],
    setMessages: vi.fn(),
    loading: false,
    error: null,
    conversation: null,
  }),
}));

vi.mock('../hooks/useStreamingResponse', () => ({
  useStreamingResponse: () => ({
    streamingContent: streamingStateRef.current.streamingContent,
    streamingSources: [],
    isStreaming: streamingStateRef.current.isStreaming,
    startStream: vi.fn().mockImplementation(async (conversationId, content, onComplete) => {
      // Actually call the real fetch logic to properly test error handling
      const res = await fetch(`/api/conversations/${conversationId}/messages`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      if (!res.body) throw new Error('No response body');

      // Simulate successful SSE completion
      onComplete({ fullText: 'Test response', sources: [] });
    }),
    abortStream: vi.fn(),
  }),
}));

vi.mock('../hooks/useToast', () => ({
  useToast: () => ({
    addToast: addToastRef.current,
    removeToast: vi.fn(),
  }),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 'test-user', email: 'test@test', is_admin: false },
    refresh: vi.fn(),
  }),
}));

// Mock scrollIntoView for jsdom
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

// Mutable ref captured by the useToast mock factory - updated in beforeEach
const addToastRef = { current: vi.fn() };
const streamingStateRef = { current: { isStreaming: false, streamingContent: '' } };

describe('ChatArea refreshConversationsRef', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getConversations').mockResolvedValue([]);
    // Reset addToastRef to a fresh spy for each test
    addToastRef.current = vi.fn();
    // Reset streaming state ref
    streamingStateRef.current = { isStreaming: false, streamingContent: '' };
  });

  /**
   * Create a mock ReadableStream for SSE response
   */
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

  it('should call refreshConversationsRef after successful message send', async () => {
    const mockRefetch = vi.fn().mockResolvedValue(undefined);
    const refreshConversationsRef = { current: mockRefetch };

    // Mock the streaming fetch response
    const mockResponse = {
      ok: true,
      status: 200,
      body: createSSEStream('Test response'),
    };
    vi.spyOn(global, 'fetch').mockResolvedValue(mockResponse as unknown as Response);

    render(
      <MemoryRouter>
        <ChatArea
          conversationId="conv-1"
          refreshConversationsRef={
            refreshConversationsRef as React.MutableRefObject<(() => Promise<void>) | null>
          }
        />
      </MemoryRouter>,
    );

    // Wait for ChatInput to be ready
    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    // Type and send a message
    const input = screen.getByRole('textbox');
    const sendButton = screen.getByRole('button', { name: /send/i });

    fireEvent.change(input, { target: { value: 'Hello test message' } });
    fireEvent.click(sendButton);

    // Verify refreshConversationsRef was called once after message send
    await waitFor(
      () => {
        expect(mockRefetch).toHaveBeenCalledTimes(1);
      },
      { timeout: 2000 },
    );
  });

  it('should NOT call refreshConversationsRef when send fails', async () => {
    const mockRefetch = vi.fn().mockResolvedValue(undefined);
    const refreshConversationsRef = { current: mockRefetch };

    // Mock fetch to return an error
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      body: null,
    } as unknown as Response);

    render(
      <MemoryRouter>
        <ChatArea
          conversationId="conv-1"
          refreshConversationsRef={
            refreshConversationsRef as React.MutableRefObject<(() => Promise<void>) | null>
          }
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    const input = screen.getByRole('textbox');
    const sendButton = screen.getByRole('button', { name: /send/i });

    fireEvent.change(input, { target: { value: 'Test message' } });
    fireEvent.click(sendButton);

    // Wait a bit for any async operations
    await new Promise((resolve) => setTimeout(resolve, 100));

    // refreshConversationsRef should NOT have been called on error
    expect(mockRefetch).not.toHaveBeenCalled();
  });

  it('should handle undefined refreshConversationsRef gracefully', async () => {
    // This tests that refreshConversationsRef?.current?.() doesn't throw
    // when ref is undefined/null

    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: createSSEStream('Response'),
    } as unknown as Response);

    // No error should be thrown when refreshConversationsRef is undefined
    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" refreshConversationsRef={undefined} />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    const input = screen.getByRole('textbox');
    const sendButton = screen.getByRole('button', { name: /send/i });

    fireEvent.change(input, { target: { value: 'Test' } });

    // Should not throw even though refreshConversationsRef is undefined
    expect(() => fireEvent.click(sendButton)).not.toThrow();
  });

  it('should defer scrollToBottom inside requestAnimationFrame when autoScrollRef is true', async () => {
    const mockRefetch = vi.fn().mockResolvedValue(undefined);
    const refreshConversationsRef = { current: mockRefetch };

    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: createSSEStream('Test response'),
    } as unknown as Response);

    // Spy on requestAnimationFrame
    let rafCallback: ((time: number) => void) | null = null;
    const mockRaf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      rafCallback = cb as (time: number) => void;
      return 1;
    });

    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView');

    render(
      <MemoryRouter>
        <ChatArea
          conversationId="conv-1"
          refreshConversationsRef={
            refreshConversationsRef as React.MutableRefObject<(() => Promise<void>) | null>
          }
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    // Verify requestAnimationFrame was called
    expect(mockRaf).toHaveBeenCalled();

    // Call the RAF callback to simulate the next paint cycle
    if (rafCallback) {
      (rafCallback as (time: number) => void)(0);
    }

    // Verify scrollIntoView was called AFTER RAF
    expect(scrollSpy).toHaveBeenCalled();
  });

  it('should create conversation and navigate when sending with no conversationId', async () => {
    const mockConv = { id: 'new-conv-123', title: 'New Chat', created_at: '', updated_at: '' };
    vi.spyOn(api, 'createConversation').mockResolvedValue(mockConv as api.Conversation);

    render(
      <MemoryRouter>
        <ChatArea conversationId={undefined} refreshConversationsRef={undefined} />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole('textbox')).toBeInTheDocument());

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'Hello world' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => {
      expect(api.createConversation).toHaveBeenCalledTimes(1);
    });

    // Issue: first-send race fix — the user's message rides along as
    // React Router state so the next-mount ChatArea can dispatch it.
    // Without this, `pendingMessageRef` was reset across the route change
    // and the message was silently dropped.
    expect(mockNavigate).toHaveBeenCalledWith('/c/new-conv-123', {
      state: { initialMessage: 'Hello world' },
    });
  });

  it('should handle createConversation error gracefully', async () => {
    vi.spyOn(api, 'createConversation').mockRejectedValue(new Error('Server error'));

    render(
      <MemoryRouter>
        <ChatArea conversationId={undefined} />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole('textbox')).toBeInTheDocument());

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Test' } });
    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    // Wait for the error to propagate
    await new Promise((r) => setTimeout(r, 200));

    expect(addToastRef.current).toHaveBeenCalledWith(
      'Could not create conversation. Please try again.',
      'error',
    );
  });

  // Regression test for issue #205: chip-driven first-send left EmptyState visible
  // because the showMessages branch rendered EmptyState whenever messages.length === 0,
  // even during the window between route-change and the dispatchedInitialRef effect firing.
  it('should not render EmptyState when location.state.initialMessage is present (#205)', async () => {
    // Prevent the dispatchedInitialRef effect's handleSend from making real fetch calls
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      body: new ReadableStream({
        start(c) {
          c.close();
        },
      }),
    } as unknown as Response);

    render(
      <MemoryRouter
        initialEntries={[
          { pathname: '/c/conv-1', state: { initialMessage: 'How do subagents work?' } },
        ]}
      >
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    // Even though messages === [] on first render, EmptyState must not appear
    // because location.state.initialMessage signals a first-send in flight.
    expect(screen.queryByText('Ask anything about the video library')).not.toBeInTheDocument();
  });

  // Regression tests for issue #215: auto-scroll behavior during streaming
  it('should use instant scroll behavior during streaming', async () => {
    streamingStateRef.current = { isStreaming: true, streamingContent: 'token' };

    let rafCallback: ((time: number) => void) | null = null;
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      rafCallback = cb as (time: number) => void;
      return 1;
    });

    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView');

    render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    // Execute the RAF callback to trigger the scroll
    if (rafCallback) {
      (rafCallback as (time: number) => void)(0);
    }

    // Verify scrollIntoView was called with 'instant' behavior during streaming
    expect(scrollSpy).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: 'instant', block: 'end' }),
    );
  });

  it('should not auto-scroll when user has scrolled up', async () => {
    streamingStateRef.current = { isStreaming: true, streamingContent: 'token1' };

    const rafCallbacks: Array<(time: number) => void> = [];
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      rafCallbacks.push(cb as (time: number) => void);
      return rafCallbacks.length;
    });

    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView');

    const { container, rerender } = render(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole('textbox')).toBeInTheDocument();
    });

    // Flush all pending RAF callbacks from initial render
    rafCallbacks.splice(0, rafCallbacks.length).forEach((cb) => cb(0));

    // Clear spy to focus on post-scroll behavior
    scrollSpy.mockClear();

    // Simulate user scrolling up by firing scroll event with distFromBottom >= 100
    // The scroll container is the parent of the bottom-ref div (height: 1px)
    const bottomDiv = container.querySelector('div[style*="height: 1px"]');
    const scrollContainer = bottomDiv?.parentElement;
    expect(scrollContainer).toBeTruthy();
    if (scrollContainer) {
      vi.spyOn(scrollContainer, 'scrollHeight', 'get').mockReturnValue(1000);
      vi.spyOn(scrollContainer, 'scrollTop', 'get').mockReturnValue(0);
      vi.spyOn(scrollContainer, 'clientHeight', 'get').mockReturnValue(500);

      scrollContainer.dispatchEvent(new Event('scroll', { bubbles: false }));
    }

    // Change streaming content to trigger a re-render
    streamingStateRef.current.streamingContent = 'token2';
    rerender(
      <MemoryRouter>
        <ChatArea conversationId="conv-1" />
      </MemoryRouter>,
    );

    // Flush any RAF callbacks queued after re-render
    rafCallbacks.splice(0, rafCallbacks.length).forEach((cb) => cb(0));

    // After scrolling up, scrollIntoView should NOT have been called again
    expect(scrollSpy).not.toHaveBeenCalled();
  });
});

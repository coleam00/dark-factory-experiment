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
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatArea } from '../components/ChatArea';
import * as api from '../lib/api';

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
    streamingContent: '',
    streamingSources: [],
    isStreaming: false,
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
    addToast: vi.fn(),
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

describe('ChatArea refreshConversationsRef', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getConversations').mockResolvedValue([]);
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
      <ChatArea
        conversationId="conv-1"
        refreshConversationsRef={
          refreshConversationsRef as React.MutableRefObject<(() => Promise<void>) | null>
        }
      />,
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
      <ChatArea
        conversationId="conv-1"
        refreshConversationsRef={
          refreshConversationsRef as React.MutableRefObject<(() => Promise<void>) | null>
        }
      />,
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
    render(<ChatArea conversationId="conv-1" refreshConversationsRef={undefined} />);

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
      <ChatArea
        conversationId="conv-1"
        refreshConversationsRef={
          refreshConversationsRef as React.MutableRefObject<(() => Promise<void>) | null>
        }
      />,
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
});

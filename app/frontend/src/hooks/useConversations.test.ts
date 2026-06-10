import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../lib/api';
import { useConversations } from './useConversations';

describe('useConversations', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rename', () => {
    it('should optimistically update conversation title', async () => {
      const conversations = [
        { id: '1', title: 'Old Title', created_at: '', updated_at: '', preview: 'Hello' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValueOnce(conversations as api.Conversation[]);
      vi.spyOn(api, 'renameConversation').mockResolvedValueOnce({} as api.Conversation);

      const { result } = renderHook(() => useConversations());
      await waitFor(() => expect(result.current.conversations).toHaveLength(1));

      const { ok } = await result.current.rename('1', 'New Title');

      expect(ok).toBe(true);
      await waitFor(() =>
        expect(result.current.conversations.find((c) => c.id === '1')?.title).toBe('New Title'),
      );
    });

    it('should revert on API failure and return error', async () => {
      const conversations = [
        { id: '1', title: 'Original', created_at: '', updated_at: '', preview: 'Hello' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValueOnce(conversations as api.Conversation[]);
      vi.spyOn(api, 'renameConversation').mockRejectedValueOnce(new Error('Network error'));

      const { result } = renderHook(() => useConversations());
      await waitFor(() => expect(result.current.conversations).toHaveLength(1));

      const { ok, error } = await result.current.rename('1', 'New Title');

      expect(ok).toBe(false);
      expect(error).toBe('Network error');
      expect(result.current.conversations.find((c) => c.id === '1')?.title).toBe('Original');
    });
  });

  describe('load error handling', () => {
    it('sets error and clears loading when load fails', async () => {
      vi.spyOn(api, 'getConversations').mockRejectedValueOnce(new Error('Network error'));

      const { result } = renderHook(() => useConversations());

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.error).toBe('Network error');
      expect(result.current.filteredConversations).toEqual([]);
    });

    it('drops stale responses when concurrent loads overlap', async () => {
      let resolveStale: (v: api.Conversation[]) => void = () => {};
      const stalePromise = new Promise<api.Conversation[]>((r) => {
        resolveStale = r;
      });

      vi.spyOn(api, 'getConversations')
        .mockReturnValueOnce(stalePromise)
        .mockResolvedValueOnce([
          { id: 'fresh', title: 'Fresh', created_at: '', updated_at: '', preview: 'X' },
        ] as api.Conversation[]);

      const { result } = renderHook(() => useConversations());

      // Trigger overlap: kick off a second refetch before the first (stale) resolves.
      void result.current.refetch();

      // Fresh response lands first.
      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('fresh');

      // Now resolve the stale promise — its result must NOT overwrite the fresh data.
      resolveStale([
        { id: 'stale', title: 'Stale', created_at: '', updated_at: '', preview: 'Y' },
      ] as api.Conversation[]);
      await new Promise((r) => setTimeout(r, 0));

      expect(result.current.filteredConversations[0].id).toBe('fresh');
    });
  });

  describe('empty conversation filtering', () => {
    it('filters out conversations with no messages (preview === null)', async () => {
      const conversations = [
        { id: '1', title: 'Chat A', created_at: '', updated_at: '', preview: 'Hello' },
        { id: '2', title: 'New Conversation', created_at: '', updated_at: '', preview: null },
        { id: '3', title: 'Chat B', created_at: '', updated_at: '', preview: 'World' },
        // Empty-string preview must pass through — strict null check, not falsy check.
        { id: '4', title: 'Chat C', created_at: '', updated_at: '', preview: '' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations());

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(3));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['1', '3', '4']);
      // conversations (unfiltered) still contains all four for guard logic
      expect(result.current.conversations).toHaveLength(4);
    });

    it('includes a conversation after its first message is sent', async () => {
      const conversations = [
        { id: '1', title: 'New Conversation', created_at: '', updated_at: '', preview: null },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations());
      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(0));

      // Simulate first message arriving (refetch returns updated data)
      const updated = [
        {
          id: '1',
          title: 'New Conversation',
          created_at: '',
          updated_at: '',
          preview: 'First message',
        },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(updated as api.Conversation[]);
      await result.current.refetch();

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
    });
  });

  describe('client-side search', () => {
    it('filters conversations by title case-insensitively', async () => {
      const conversations = [
        { id: '1', title: 'Python Tutorial', created_at: '', updated_at: '', preview: 'Hello' },
        { id: '2', title: 'JavaScript Guide', created_at: '', updated_at: '', preview: 'Hi' },
        { id: '3', title: 'python advanced', created_at: '', updated_at: '', preview: 'Hey' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations('python'));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(2));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['1', '3']);
    });

    it('excludes empty conversations from search results', async () => {
      const conversations = [
        { id: '1', title: 'New Conversation', created_at: '', updated_at: '', preview: null },
        {
          id: '2',
          title: 'New Conversation',
          created_at: '',
          updated_at: '',
          preview: 'Has messages',
        },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations('New'));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('2');
    });

    it('returns full list when query is empty', async () => {
      const conversations = [
        { id: '1', title: 'Chat A', created_at: '', updated_at: '', preview: 'Hello' },
        { id: '2', title: 'Chat B', created_at: '', updated_at: '', preview: 'World' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations(''));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(2));
    });

    it('trims whitespace-only queries and returns full list', async () => {
      const conversations = [
        { id: '1', title: 'Chat A', created_at: '', updated_at: '', preview: 'Hello' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations('   '));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
    });

    it('returns empty list when query has no matches', async () => {
      const conversations = [
        { id: '1', title: 'Python Tutorial', created_at: '', updated_at: '', preview: 'Hello' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations('rust'));

      // Wait for the underlying load to settle, then assert the filter returns []
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations).toHaveLength(0);
    });

    it('trims leading/trailing whitespace from non-empty queries', async () => {
      const conversations = [
        { id: '1', title: 'Python Tutorial', created_at: '', updated_at: '', preview: 'Hello' },
        { id: '2', title: 'JavaScript', created_at: '', updated_at: '', preview: 'Hi' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations('  python  '));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('1');
    });
  });

  describe('server-side filters (date / video)', () => {
    it('calls searchConversations and surfaces its result when videoId is set', async () => {
      const getSpy = vi.spyOn(api, 'getConversations');
      const searchSpy = vi.spyOn(api, 'searchConversations').mockResolvedValue([
        { id: 'v1', title: 'About a video', created_at: '', updated_at: '', preview: 'Hi' },
        // preview === null must still be filtered out on the server-side path
        { id: 'v2', title: 'Empty', created_at: '', updated_at: '', preview: null },
      ] as api.Conversation[]);

      const { result } = renderHook(() =>
        useConversations(undefined, { videoId: 'vid-123' }),
      );

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('v1');
      expect(searchSpy).toHaveBeenCalledWith({
        q: undefined,
        dateFrom: undefined,
        dateTo: undefined,
        videoId: 'vid-123',
      });
      expect(getSpy).not.toHaveBeenCalled();
    });

    it('passes date range params through to searchConversations', async () => {
      const searchSpy = vi
        .spyOn(api, 'searchConversations')
        .mockResolvedValue([] as api.Conversation[]);

      const { result } = renderHook(() =>
        useConversations('react', { dateFrom: '2026-01-01', dateTo: '2026-02-01' }),
      );

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(searchSpy).toHaveBeenCalledWith({
        q: 'react',
        dateFrom: '2026-01-01',
        dateTo: '2026-02-01',
        videoId: undefined,
      });
    });

    it('does not apply client-side title filtering on the server-side path', async () => {
      // The server already applied the title match; the client must surface the
      // server result verbatim (minus preview === null), not re-filter by title.
      vi.spyOn(api, 'searchConversations').mockResolvedValue([
        { id: '1', title: 'Totally unrelated', created_at: '', updated_at: '', preview: 'Hi' },
      ] as api.Conversation[]);

      const { result } = renderHook(() =>
        useConversations('python', { videoId: 'vid-9' }),
      );

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('1');
    });

    it('re-fetches when a filter changes', async () => {
      const searchSpy = vi
        .spyOn(api, 'searchConversations')
        .mockResolvedValueOnce([
          { id: 'a', title: 'A', created_at: '', updated_at: '', preview: 'Hi' },
        ] as api.Conversation[])
        .mockResolvedValueOnce([
          { id: 'b', title: 'B', created_at: '', updated_at: '', preview: 'Yo' },
        ] as api.Conversation[]);

      const { result, rerender } = renderHook(
        ({ videoId }: { videoId: string }) => useConversations(undefined, { videoId }),
        { initialProps: { videoId: 'vid-1' } },
      );

      await waitFor(() => expect(result.current.filteredConversations[0]?.id).toBe('a'));

      rerender({ videoId: 'vid-2' });

      await waitFor(() => expect(result.current.filteredConversations[0]?.id).toBe('b'));
      expect(searchSpy).toHaveBeenCalledTimes(2);
    });

    it('uses getConversations (not the server endpoint) when no filters are set', async () => {
      const getSpy = vi.spyOn(api, 'getConversations').mockResolvedValue([
        { id: '1', title: 'Chat', created_at: '', updated_at: '', preview: 'Hello' },
      ] as api.Conversation[]);
      const searchSpy = vi.spyOn(api, 'searchConversations');

      const { result } = renderHook(() => useConversations('chat'));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(getSpy).toHaveBeenCalled();
      expect(searchSpy).not.toHaveBeenCalled();
    });
  });
});

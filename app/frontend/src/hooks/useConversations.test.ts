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

      const { result } = renderHook(() => useConversations({ searchQuery: 'python' }));

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

      const { result } = renderHook(() => useConversations({ searchQuery: 'New' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('2');
    });

    it('returns full list when query is empty', async () => {
      const conversations = [
        { id: '1', title: 'Chat A', created_at: '', updated_at: '', preview: 'Hello' },
        { id: '2', title: 'Chat B', created_at: '', updated_at: '', preview: 'World' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations({ searchQuery: '' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(2));
    });

    it('trims whitespace-only queries and returns full list', async () => {
      const conversations = [
        { id: '1', title: 'Chat A', created_at: '', updated_at: '', preview: 'Hello' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations({ searchQuery: '   ' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
    });

    it('returns empty list when query has no matches', async () => {
      const conversations = [
        { id: '1', title: 'Python Tutorial', created_at: '', updated_at: '', preview: 'Hello' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations({ searchQuery: 'rust' }));

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

      const { result } = renderHook(() => useConversations({ searchQuery: '  python  ' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('1');
    });
  });

  describe('date filter', () => {
    // Build ISO strings from local-time dates so the test is robust across timezones.
    const l = (y: number, m: number, d: number, h: number, min: number) =>
      new Date(y, m - 1, d, h, min).toISOString();

    const conversations = [
      {
        id: '1',
        title: 'Yesterday',
        created_at: l(2024, 1, 10, 0, 0),
        updated_at: l(2024, 1, 14, 10, 0),
        preview: 'Hello',
      },
      {
        id: '2',
        title: 'Today morning',
        created_at: l(2024, 1, 15, 0, 0),
        updated_at: l(2024, 1, 15, 8, 0),
        preview: 'Hi',
      },
      {
        id: '3',
        title: 'Today night',
        created_at: l(2024, 1, 15, 0, 0),
        updated_at: l(2024, 1, 15, 23, 59),
        preview: 'Hey',
      },
      {
        id: '4',
        title: 'Tomorrow',
        created_at: l(2024, 1, 15, 0, 0),
        updated_at: l(2024, 1, 16, 5, 0),
        preview: 'Hola',
      },
    ];

    beforeEach(() => {
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);
    });

    it('filters by from-only (inclusive)', async () => {
      const { result } = renderHook(() =>
        useConversations({ dateFrom: '2024-01-15', dateTo: null }),
      );
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['2', '3', '4']);
    });

    it('filters by to-only (inclusive)', async () => {
      const { result } = renderHook(() =>
        useConversations({ dateFrom: null, dateTo: '2024-01-15' }),
      );
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['1', '2', '3']);
    });

    it('filters by both bounds (inclusive)', async () => {
      const { result } = renderHook(() =>
        useConversations({ dateFrom: '2024-01-14', dateTo: '2024-01-15' }),
      );
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['1', '2', '3']);
    });

    it('excludes conversations outside range', async () => {
      const { result } = renderHook(() =>
        useConversations({ dateFrom: '2024-01-16', dateTo: '2024-01-16' }),
      );
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['4']);
    });
  });

  describe('video filter', () => {
    const conversations = [
      { id: 'a', title: 'Chat A', created_at: '', updated_at: '', preview: 'Hello' },
      { id: 'b', title: 'Chat B', created_at: '', updated_at: '', preview: 'Hi' },
      { id: 'c', title: 'Chat C', created_at: '', updated_at: '', preview: 'Hey' },
    ];

    beforeEach(() => {
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);
    });

    it('only passes conversations with a ref for the selected video', async () => {
      vi.spyOn(api, 'getConversationVideoRefs').mockResolvedValue([
        { conversation_id: 'a', video_id: 'v1' },
        { conversation_id: 'b', video_id: 'v2' },
      ]);

      const { result } = renderHook(() => useConversations({ videoId: 'v1' }));
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['a']);
    });

    it('excludes conversations with no refs', async () => {
      vi.spyOn(api, 'getConversationVideoRefs').mockResolvedValue([
        { conversation_id: 'a', video_id: 'v1' },
      ]);

      const { result } = renderHook(() => useConversations({ videoId: 'v1' }));
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['a']);
    });

    it('does not fetch refs when no videoId is set', async () => {
      const spy = vi.spyOn(api, 'getConversationVideoRefs').mockResolvedValue([]);
      renderHook(() => useConversations());
      expect(spy).not.toHaveBeenCalled();
    });

    it('sets error on refs fetch failure', async () => {
      vi.spyOn(api, 'getConversationVideoRefs').mockRejectedValue(new Error('Network error'));

      const { result } = renderHook(() => useConversations({ videoId: 'v1' }));
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.error).toBe('Network error');
      // When refs fail to load, videoRefs stays null so the video filter is not applied.
      expect(result.current.filteredConversations).toHaveLength(3);
    });
  });

  describe('combined filters', () => {
    const conversations = [
      {
        id: 'a',
        title: 'Python Tutorial',
        created_at: '',
        updated_at: '2024-01-15T10:00:00Z',
        preview: 'Hello',
      },
      {
        id: 'b',
        title: 'Python Advanced',
        created_at: '',
        updated_at: '2024-01-10T10:00:00Z',
        preview: 'Hi',
      },
      {
        id: 'c',
        title: 'Rust Guide',
        created_at: '',
        updated_at: '2024-01-15T10:00:00Z',
        preview: 'Hey',
      },
    ];

    beforeEach(() => {
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);
    });

    it('text + date + video together yield intersection', async () => {
      vi.spyOn(api, 'getConversationVideoRefs').mockResolvedValue([
        { conversation_id: 'a', video_id: 'v1' },
        { conversation_id: 'c', video_id: 'v1' },
      ]);

      const { result } = renderHook(() =>
        useConversations({
          searchQuery: 'python',
          dateFrom: '2024-01-15',
          dateTo: '2024-01-15',
          videoId: 'v1',
        }),
      );
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['a']);
    });

    it('preserves server order', async () => {
      vi.spyOn(api, 'getConversationVideoRefs').mockResolvedValue([
        { conversation_id: 'c', video_id: 'v1' },
        { conversation_id: 'a', video_id: 'v1' },
      ]);

      const { result } = renderHook(() => useConversations({ videoId: 'v1' }));
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['a', 'c']);
    });
  });
});

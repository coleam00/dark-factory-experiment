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

  describe('date filter', () => {
    it('keeps conversations within the date range', async () => {
      const conversations = [
        { id: '1', title: 'Old Chat', created_at: '', updated_at: '2026-05-01T12:00:00Z', preview: 'Hi', video_ids: null },
        { id: '2', title: 'Recent Chat', created_at: '', updated_at: '2026-05-20T12:00:00Z', preview: 'Hello', video_ids: null },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const from = new Date('2026-05-15');
      const to = new Date('2026-05-25');
      const { result } = renderHook(() => useConversations('', { dateFrom: from, dateTo: to }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('2');
    });

    it('is inclusive on the bounds', async () => {
      const conversations = [
        { id: '1', title: 'Edge Morning', created_at: '', updated_at: '2026-05-15T12:00:00Z', preview: 'A', video_ids: null },
        { id: '2', title: 'Edge Night', created_at: '', updated_at: '2026-05-16T12:00:00Z', preview: 'B', video_ids: null },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const from = new Date('2026-05-15');
      const to = new Date('2026-05-16');
      const { result } = renderHook(() => useConversations('', { dateFrom: from, dateTo: to }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(2));
    });

    it('drops out-of-range conversations', async () => {
      const conversations = [
        { id: '1', title: 'Too old', created_at: '', updated_at: '2026-04-01T12:00:00Z', preview: 'X', video_ids: null },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const from = new Date('2026-05-01');
      const { result } = renderHook(() => useConversations('', { dateFrom: from }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(0));
    });
  });

  describe('video filter', () => {
    it('keeps conversations that reference the video', async () => {
      const conversations = [
        { id: '1', title: 'About A', created_at: '', updated_at: '', preview: 'Hi', video_ids: ['vid-a'] },
        { id: '2', title: 'About B', created_at: '', updated_at: '', preview: 'Hello', video_ids: ['vid-b'] },
        { id: '3', title: 'No videos', created_at: '', updated_at: '', preview: 'Hey', video_ids: null },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations('', { videoId: 'vid-a' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('1');
    });
  });

  describe('combined filters', () => {
    it('applies text, date and video filters with AND', async () => {
      const conversations = [
        { id: '1', title: 'Python A', created_at: '', updated_at: '2026-05-20T12:00:00Z', preview: 'Hi', video_ids: ['vid-a'] },
        { id: '2', title: 'Python B', created_at: '', updated_at: '2026-05-10T12:00:00Z', preview: 'Hi', video_ids: ['vid-a'] },
        { id: '3', title: 'Rust A', created_at: '', updated_at: '2026-05-20T12:00:00Z', preview: 'Hi', video_ids: ['vid-a'] },
        { id: '4', title: 'Python A', created_at: '', updated_at: '2026-05-20T12:00:00Z', preview: 'Hi', video_ids: ['vid-b'] },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const from = new Date('2026-05-15');
      const { result } = renderHook(() => useConversations('python', { dateFrom: from, videoId: 'vid-a' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('1');
    });

    it('preserves most-recent-first order', async () => {
      const conversations = [
        { id: '1', title: 'Old', created_at: '', updated_at: '2026-05-01T12:00:00Z', preview: 'X', video_ids: ['vid-a'] },
        { id: '2', title: 'New', created_at: '', updated_at: '2026-05-20T12:00:00Z', preview: 'Y', video_ids: ['vid-a'] },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations('', { videoId: 'vid-a' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(2));
      expect(result.current.filteredConversations.map((c) => c.id)).toEqual(['2', '1']);
    });

    it('excludes empty conversations even when filters match', async () => {
      const conversations = [
        { id: '1', title: 'Empty', created_at: '', updated_at: '2026-05-20T12:00:00Z', preview: null, video_ids: ['vid-a'] },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations('', { videoId: 'vid-a' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(0));
    });
  });
});

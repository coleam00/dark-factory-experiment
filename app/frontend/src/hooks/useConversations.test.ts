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

  describe('server-side filters (issue #294)', () => {
    it('passes the filter object through to getConversations', async () => {
      const spy = vi.spyOn(api, 'getConversations').mockResolvedValue([] as api.Conversation[]);

      const filters = { q: 'python', date_from: '2026-06-01', video_id: 'vid-1' };
      const { result } = renderHook(() => useConversations(filters));

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(spy).toHaveBeenCalledWith(filters);
    });

    it('calls getConversations with an empty object when no filters are given', async () => {
      const spy = vi.spyOn(api, 'getConversations').mockResolvedValue([] as api.Conversation[]);

      const { result } = renderHook(() => useConversations());

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(spy).toHaveBeenCalledWith({});
    });

    it('refetches when a filter value changes', async () => {
      const spy = vi.spyOn(api, 'getConversations').mockResolvedValue([] as api.Conversation[]);

      const { result, rerender } = renderHook(({ filters }) => useConversations(filters), {
        initialProps: { filters: { q: 'python' } as api.ConversationFilters },
      });
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(spy).toHaveBeenCalledTimes(1);

      rerender({ filters: { q: 'python', video_id: 'vid-1' } });

      await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
      expect(spy).toHaveBeenLastCalledWith({ q: 'python', video_id: 'vid-1' });
    });

    it('does not refetch when an identical filter object is passed on re-render', async () => {
      const spy = vi.spyOn(api, 'getConversations').mockResolvedValue([] as api.Conversation[]);

      const { result, rerender } = renderHook(({ filters }) => useConversations(filters), {
        initialProps: { filters: { q: 'python' } as api.ConversationFilters },
      });
      await waitFor(() => expect(result.current.loading).toBe(false));

      // Fresh object, same values — must not trigger a second fetch.
      rerender({ filters: { q: 'python' } });
      await new Promise((r) => setTimeout(r, 0));

      expect(spy).toHaveBeenCalledTimes(1);
    });

    it('still excludes preview === null rows from server-filtered results', async () => {
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

      const { result } = renderHook(() => useConversations({ q: 'New' }));

      await waitFor(() => expect(result.current.filteredConversations).toHaveLength(1));
      expect(result.current.filteredConversations[0].id).toBe('2');
    });
  });
});

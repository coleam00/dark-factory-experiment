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
      const conversations = [{ id: '1', title: 'Old Title', created_at: '', updated_at: '' }];
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
      const conversations = [{ id: '1', title: 'Original', created_at: '', updated_at: '' }];
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

  describe('search', () => {
    it('calls searchConversations when query is non-empty', async () => {
      const matches = [
        { id: '1', title: 'Python Tutorial', created_at: '', updated_at: '' },
        { id: '3', title: 'python advanced', created_at: '', updated_at: '' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValue([] as api.Conversation[]);
      const spy = vi
        .spyOn(api, 'searchConversations')
        .mockResolvedValueOnce(matches as api.Conversation[]);

      const { result } = renderHook(() => useConversations('python'));

      await waitFor(() => expect(spy).toHaveBeenCalledWith('python'));
      await waitFor(() => expect(result.current.conversations).toHaveLength(2));
      expect(result.current.filteredConversations).toHaveLength(2);
    });

    it('calls getConversations (not search) when query is empty', async () => {
      const conversations = [
        { id: '1', title: 'Chat A', created_at: '', updated_at: '' },
        { id: '2', title: 'Chat B', created_at: '', updated_at: '' },
      ];
      const getSpy = vi
        .spyOn(api, 'getConversations')
        .mockResolvedValue(conversations as api.Conversation[]);
      const searchSpy = vi.spyOn(api, 'searchConversations');

      const { result } = renderHook(() => useConversations(''));

      await waitFor(() => expect(result.current.conversations).toHaveLength(2));
      expect(getSpy).toHaveBeenCalled();
      expect(searchSpy).not.toHaveBeenCalled();
    });

    it('trims whitespace-only queries and falls back to full list', async () => {
      vi.spyOn(api, 'getConversations').mockResolvedValue([
        { id: '1', title: 'Chat A', created_at: '', updated_at: '' },
      ] as api.Conversation[]);
      const searchSpy = vi.spyOn(api, 'searchConversations');

      renderHook(() => useConversations('   '));

      await waitFor(() => expect(api.getConversations).toHaveBeenCalled());
      expect(searchSpy).not.toHaveBeenCalled();
    });

    it('refetches when searchQuery prop changes', async () => {
      vi.spyOn(api, 'getConversations').mockResolvedValue([] as api.Conversation[]);
      const searchSpy = vi
        .spyOn(api, 'searchConversations')
        .mockResolvedValue([] as api.Conversation[]);

      const { rerender } = renderHook(({ q }: { q: string }) => useConversations(q), {
        initialProps: { q: 'alpha' },
      });
      await waitFor(() => expect(searchSpy).toHaveBeenCalledWith('alpha'));

      rerender({ q: 'beta' });
      await waitFor(() => expect(searchSpy).toHaveBeenCalledWith('beta'));
    });
  });
});

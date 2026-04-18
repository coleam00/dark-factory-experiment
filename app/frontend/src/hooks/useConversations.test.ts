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
    it('should filter conversations by title (case-insensitive)', async () => {
      const conversations = [
        { id: '1', title: 'Python Tutorial', created_at: '', updated_at: '' },
        { id: '2', title: 'JavaScript Guide', created_at: '', updated_at: '' },
        { id: '3', title: 'python advanced', created_at: '', updated_at: '' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValueOnce(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations());
      await waitFor(() => expect(result.current.conversations).toHaveLength(3));

      result.current.search('python');
      await waitFor(() => {
        expect(result.current.filteredConversations).toHaveLength(2);
      });
    });

    it('should return all conversations when search is empty', async () => {
      const conversations = [
        { id: '1', title: 'Chat A', created_at: '', updated_at: '' },
        { id: '2', title: 'Chat B', created_at: '', updated_at: '' },
      ];
      vi.spyOn(api, 'getConversations').mockResolvedValueOnce(conversations as api.Conversation[]);

      const { result } = renderHook(() => useConversations());
      await waitFor(() => expect(result.current.conversations).toHaveLength(2));

      result.current.search('');
      expect(result.current.filteredConversations).toHaveLength(2);
    });
  });
});

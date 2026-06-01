import { useCallback, useEffect, useRef, useState } from 'react';
import { type Conversation, type ConversationFilters, getConversations, renameConversation } from '../lib/api';

export function useConversations(filters?: ConversationFilters) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-fetch ID so a stale response can't overwrite fresher results
  // when the user types faster than the network replies.
  const fetchIdRef = useRef(0);

  const load = useCallback(async () => {
    const myId = ++fetchIdRef.current;
    try {
      setLoading(true);
      const data = await getConversations(filters);
      if (myId === fetchIdRef.current) setConversations(data);
    } catch (e) {
      if (myId === fetchIdRef.current) {
        setError(e instanceof Error ? e.message : 'Failed to load conversations');
      }
    } finally {
      if (myId === fetchIdRef.current) setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    load();
  }, [load]);

  const rename = async (id: string, title: string): Promise<{ ok: boolean; error?: string }> => {
    const prevConversations = conversations;
    setConversations((cs) => cs.map((c) => (c.id === id ? { ...c, title } : c)));
    try {
      await renameConversation(id, title);
      return { ok: true };
    } catch (e) {
      setConversations(prevConversations);
      const msg = e instanceof Error ? e.message : 'Rename failed';
      return { ok: false, error: msg };
    }
  };

  // Filter out conversations with zero messages (preview === null).
  // Server-side filtering already applied; this just hides empty chats.
  const filteredConversations = conversations.filter((c) => c.preview !== null);

  return {
    conversations,
    loading,
    error,
    refetch: load,
    rename,
    filteredConversations,
  };
}

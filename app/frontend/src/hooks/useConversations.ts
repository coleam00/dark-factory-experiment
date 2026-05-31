import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type Conversation,
  getConversations,
  searchConversations,
  renameConversation,
} from '../lib/api';

export function useConversations(filters?: {
  query?: string;
  dateFrom?: string;
  dateTo?: string;
  videoId?: string;
}) {
  const { query = '', dateFrom = '', dateTo = '', videoId = '' } = filters ?? {};
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
      const trimmedQuery = query.trim();
      const hasFilters = trimmedQuery || dateFrom || dateTo || videoId;

      let data: Conversation[];
      if (hasFilters) {
        // Normalize dateTo to end-of-day UTC so conversations on the
        // selected end date are included.
        const apiDateTo = dateTo
          ? new Date(`${dateTo}T23:59:59.999Z`).toISOString()
          : undefined;
        data = await searchConversations(
          trimmedQuery || undefined,
          dateFrom || undefined,
          apiDateTo,
          videoId || undefined,
        );
      } else {
        data = await getConversations();
      }

      if (myId === fetchIdRef.current) setConversations(data);
    } catch (e) {
      if (myId === fetchIdRef.current) {
        setError(e instanceof Error ? e.message : 'Failed to load conversations');
      }
    } finally {
      if (myId === fetchIdRef.current) setLoading(false);
    }
  }, [query, dateFrom, dateTo, videoId]);

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
  // Keep conversations unfiltered for guard logic in Sidebar.tsx.
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

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type Conversation,
  filterConversations,
  getConversations,
  renameConversation,
} from '../lib/api';

export interface ConversationFilters {
  dateFrom?: string; // yyyy-mm-dd local calendar day (inclusive)
  dateTo?: string; // yyyy-mm-dd local calendar day (inclusive)
  videoId?: string;
}

export function useConversations(searchQuery?: string, filters?: ConversationFilters) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-fetch ID so a stale response can't overwrite fresher results
  // when the user types faster than the network replies.
  const fetchIdRef = useRef(0);

  const dateFrom = filters?.dateFrom;
  const dateTo = filters?.dateTo;
  const videoId = filters?.videoId;
  const filtersActive = !!(dateFrom || dateTo || videoId);
  const trimmedQuery = (searchQuery ?? '').trim();
  // Only the filter path refetches on text changes (server applies `q`).
  // In the default path the client-side title filter handles search without a
  // refetch, so keep the text query out of `load`'s deps there.
  const queryDep = filtersActive ? trimmedQuery : '';

  const load = useCallback(async () => {
    const myId = ++fetchIdRef.current;
    try {
      setLoading(true);
      let data: Conversation[];
      if (filtersActive) {
        // Convert local calendar days to UTC instants. `date_to` is the start
        // of the day AFTER the chosen end day, making the upper bound exclusive
        // so there are no off-by-one-day surprises across timezones.
        const date_from = dateFrom ? new Date(`${dateFrom}T00:00:00`).toISOString() : undefined;
        let date_to: string | undefined;
        if (dateTo) {
          const next = new Date(`${dateTo}T00:00:00`);
          next.setDate(next.getDate() + 1);
          date_to = next.toISOString();
        }
        data = await filterConversations({
          q: trimmedQuery || undefined,
          date_from,
          date_to,
          video_id: videoId || undefined,
        });
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
    // trimmedQuery is captured via queryDep (only meaningful when filtersActive).
  }, [filtersActive, dateFrom, dateTo, videoId, queryDep]);

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
  const withMessages = conversations.filter((c) => c.preview !== null);

  // When server-side filters are active the backend already applied the text
  // query, so skip the client-side title filter to avoid double-filtering.
  const trimmed = trimmedQuery.toLowerCase();
  const filteredConversations =
    trimmed && !filtersActive
      ? withMessages.filter((c) => c.title.toLowerCase().includes(trimmed))
      : withMessages;

  return {
    conversations,
    loading,
    error,
    refetch: load,
    rename,
    filteredConversations,
  };
}

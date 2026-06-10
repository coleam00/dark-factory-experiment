import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type Conversation,
  getConversations,
  renameConversation,
  searchConversations,
} from '../lib/api';

export interface ConversationFilterArgs {
  dateFrom?: string;
  dateTo?: string;
  videoId?: string;
}

export function useConversations(searchQuery?: string, filters?: ConversationFilterArgs) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-fetch ID so a stale response can't overwrite fresher results
  // when the user types faster than the network replies.
  const fetchIdRef = useRef(0);

  // A date or video filter requires server-side filtering (the video link lives
  // only inside messages.sources, which the client never loads). Title-only
  // search stays on the existing client-side path so prior behavior is unchanged.
  const filtersActive = !!(filters?.dateFrom || filters?.dateTo || filters?.videoId);

  const load = useCallback(async () => {
    const myId = ++fetchIdRef.current;
    try {
      setLoading(true);
      const data = filtersActive
        ? await searchConversations({
            q: searchQuery,
            dateFrom: filters?.dateFrom,
            dateTo: filters?.dateTo,
            videoId: filters?.videoId,
          })
        : await getConversations();
      if (myId === fetchIdRef.current) setConversations(data);
    } catch (e) {
      if (myId === fetchIdRef.current) {
        setError(e instanceof Error ? e.message : 'Failed to load conversations');
      }
    } finally {
      if (myId === fetchIdRef.current) setLoading(false);
    }
    // searchQuery only affects the fetch when a server-side filter is active;
    // otherwise it's applied client-side below and must not retrigger a load.
    // Use `filtersActive ? searchQuery : null` so typing doesn't retrigger
    // getConversations() on the non-filter path (null is stable when inactive).
  }, [filtersActive, filters?.dateFrom, filters?.dateTo, filters?.videoId, filtersActive ? searchQuery : null]);

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

  // When a server-side filter is active the backend already applied the title
  // match, so skip the client-side substring filter to avoid double-filtering.
  const trimmed = (searchQuery ?? '').trim().toLowerCase();
  const filteredConversations =
    !filtersActive && trimmed
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

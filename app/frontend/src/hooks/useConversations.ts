import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type Conversation,
  getConversationVideoRefs,
  getConversations,
  renameConversation,
} from '../lib/api';

export interface ConversationFilters {
  searchQuery?: string;
  dateFrom?: string | null; // 'YYYY-MM-DD', inclusive (local start of day)
  dateTo?: string | null; // 'YYYY-MM-DD', inclusive (local end of day)
  videoId?: string | null;
}

function toLocalStartOfDay(dateStr: string): Date {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d, 0, 0, 0, 0);
}

function toLocalEndOfDay(dateStr: string): Date {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, m - 1, d, 23, 59, 59, 999);
}

export function useConversations(filters?: ConversationFilters) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-fetch ID so a stale response can't overwrite fresher results
  // when the user types faster than the network replies.
  const fetchIdRef = useRef(0);

  // videoRefs: conversation_id -> set of video_ids
  const [videoRefs, setVideoRefs] = useState<Map<string, Set<string>> | null>(null);
  const [videoRefsLoading, setVideoRefsLoading] = useState(false);
  const [videoRefsError, setVideoRefsError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const myId = ++fetchIdRef.current;
    try {
      setLoading(true);
      const data = await getConversations();
      if (myId === fetchIdRef.current) setConversations(data);
    } catch (e) {
      if (myId === fetchIdRef.current) {
        setError(e instanceof Error ? e.message : 'Failed to load conversations');
      }
    } finally {
      if (myId === fetchIdRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Lazy-fetch video refs when a video filter is active
  useEffect(() => {
    if (!filters?.videoId) {
      setVideoRefs(null);
      setVideoRefsLoading(false);
      setVideoRefsError(null);
      return;
    }

    let cancelled = false;
    setVideoRefsLoading(true);
    setVideoRefsError(null);

    getConversationVideoRefs()
      .then((refs) => {
        if (cancelled) return;
        const map = new Map<string, Set<string>>();
        for (const ref of refs) {
          const set = map.get(ref.conversation_id) ?? new Set<string>();
          set.add(ref.video_id);
          map.set(ref.conversation_id, set);
        }
        setVideoRefs(map);
        setVideoRefsLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setVideoRefsError(e instanceof Error ? e.message : 'Failed to load video refs');
        setVideoRefsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [filters?.videoId]);

  // Also re-fetch video refs on manual refetch if they were already loaded
  const refetch = useCallback(async () => {
    await load();
    if (filters?.videoId && videoRefs !== null) {
      setVideoRefsLoading(true);
      try {
        const refs = await getConversationVideoRefs();
        const map = new Map<string, Set<string>>();
        for (const ref of refs) {
          const set = map.get(ref.conversation_id) ?? new Set<string>();
          set.add(ref.video_id);
          map.set(ref.conversation_id, set);
        }
        setVideoRefs(map);
        setVideoRefsError(null);
      } catch (e) {
        setVideoRefsError(e instanceof Error ? e.message : 'Failed to load video refs');
      } finally {
        setVideoRefsLoading(false);
      }
    }
  }, [load, filters?.videoId, videoRefs]);

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

  const trimmed = (filters?.searchQuery ?? '').trim().toLowerCase();
  let filteredConversations = trimmed
    ? withMessages.filter((c) => c.title.toLowerCase().includes(trimmed))
    : withMessages;

  // Date filter
  const dateFrom = filters?.dateFrom;
  const dateTo = filters?.dateTo;
  if (dateFrom || dateTo) {
    filteredConversations = filteredConversations.filter((c) => {
      const updated = new Date(c.updated_at);
      if (dateFrom) {
        const start = toLocalStartOfDay(dateFrom);
        if (updated < start) return false;
      }
      if (dateTo) {
        const end = toLocalEndOfDay(dateTo);
        if (updated > end) return false;
      }
      return true;
    });
  }

  // Video filter
  const selectedVideoId = filters?.videoId;
  if (selectedVideoId && videoRefs !== null) {
    filteredConversations = filteredConversations.filter((c) =>
      videoRefs.get(c.id)?.has(selectedVideoId),
    );
  }

  const isLoading = loading || (filters?.videoId ? videoRefsLoading : false);
  const combinedError = error || (filters?.videoId ? videoRefsError : null);

  return {
    conversations,
    loading: isLoading,
    error: combinedError,
    refetch,
    rename,
    filteredConversations,
  };
}

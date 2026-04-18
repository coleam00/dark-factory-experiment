import { useEffect, useState } from 'react';
import { type Conversation, getConversations, renameConversation } from '../lib/api';

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  const fetchConversations = async () => {
    try {
      setLoading(true);
      const data = await getConversations();
      setConversations(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load conversations');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConversations();
  }, []);

  const rename = async (id: string, title: string): Promise<boolean> => {
    const prev = conversations;
    setConversations((cs) => cs.map((c) => (c.id === id ? { ...c, title } : c)));
    try {
      await renameConversation(id, title);
      return true;
    } catch {
      setConversations(prev);
      return false;
    }
  };

  const search = (q: string) => setSearchQuery(q);

  const filtered = searchQuery
    ? conversations.filter((c) => c.title.toLowerCase().includes(searchQuery.toLowerCase()))
    : conversations;

  return { conversations, loading, error, refetch: fetchConversations, rename, search, filteredConversations: filtered };
}

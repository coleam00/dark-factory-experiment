import { useEffect, useState } from 'react';
import { type Conversation, type Message, getConversation } from '../lib/api';

export function useMessages(conversationId: string | null) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      setConversation(null);
      return;
    }
    setLoading(true);
    setError(null);
    getConversation(conversationId)
      .then((data) => {
        setMessages(data.messages);
        setConversation({
          id: data.id,
          title: data.title,
          created_at: data.created_at,
          updated_at: data.updated_at,
        });
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load messages'))
      .finally(() => setLoading(false));
  }, [conversationId]);

  return { messages, setMessages, loading, error, conversation };
}

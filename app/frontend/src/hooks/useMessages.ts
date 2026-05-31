import { useEffect, useState } from 'react';
import { ApiError, type Conversation, type Message, getConversation } from '../lib/api';

export function useMessages(conversationId: string | null) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 404 from GET /conversations/<id> is shaped differently than a transient
  // network error: the conversation truly does not exist (or isn't owned by
  // this user). Surface it as a flag so ChatArea can render a friendly
  // "Conversation not found" UI instead of dumping the raw API error.
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      setConversation(null);
      setNotFound(false);
      return;
    }
    setLoading(true);
    setError(null);
    setNotFound(false);
    getConversation(conversationId)
      .then((data) => {
        setMessages(data.messages);
        setConversation({
          id: data.id,
          title: data.title,
          created_at: data.created_at,
          updated_at: data.updated_at,
          scoped_video_ids: data.scoped_video_ids,
        });
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) {
          setNotFound(true);
          setError(null);
          return;
        }
        setError(e instanceof Error ? e.message : 'Failed to load messages');
      })
      .finally(() => setLoading(false));
  }, [conversationId]);

  return { messages, setMessages, loading, error, notFound, conversation };
}

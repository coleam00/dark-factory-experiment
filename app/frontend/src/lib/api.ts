/**
 * Typed fetch wrappers for the RAG YouTube Chat API.
 */

const BASE = '/api';

export interface Video {
  id: string;
  title: string;
  description: string;
  url: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  preview?: string | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  /** RAG source video titles — only populated for freshly-streamed assistant messages */
  sources?: string[];
}

export interface ConversationWithMessages extends Conversation {
  messages: Message[];
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (res.status === 401) {
    // Session missing/expired — bounce to login, preserving return path.
    if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
      const returnTo = window.location.pathname + window.location.search;
      window.location.assign(`/login?from=${encodeURIComponent(returnTo)}`);
    }
    throw new Error('Not authenticated');
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// Conversations
export const getConversations = () => request<Conversation[]>('/conversations');
export const createConversation = () =>
  request<Conversation>('/conversations', { method: 'POST', body: '{}' });
export const getConversation = (id: string) =>
  request<ConversationWithMessages>(`/conversations/${id}`);
export const deleteConversation = (id: string) =>
  fetch(`${BASE}/conversations/${id}`, { method: 'DELETE', credentials: 'include' });

// Videos
export const getVideos = () => request<Video[]>('/videos');

export interface IngestVideoBody {
  title: string;
  description: string;
  url: string;
  transcript: string;
}

export interface IngestVideoResponse {
  video_id: string;
  chunks_created: number;
  status: string;
}

export const ingestVideo = (body: IngestVideoBody) =>
  request<IngestVideoResponse>('/ingest', {
    method: 'POST',
    body: JSON.stringify(body),
  });

// Health
export const getHealth = () =>
  request<{ status: string; video_count: number; chunk_count: number; db_path: string }>('/health');

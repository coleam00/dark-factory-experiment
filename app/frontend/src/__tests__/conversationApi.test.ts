import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getConversationVideos, searchConversations } from '../lib/api';

describe('conversation filter api wrappers', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockJson(body: unknown, status = 200) {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: status >= 200 && status < 300,
      status,
      statusText: 'OK',
      json: async () => body,
      text: async () => JSON.stringify(body),
    });
  }

  function calledUrl(): string {
    return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
  }

  it('searchConversations with no filters hits the bare search endpoint', async () => {
    mockJson([]);
    await searchConversations();
    expect(calledUrl()).toBe('/api/conversations/search');
  });

  it('searchConversations encodes every filter into query params', async () => {
    mockJson([]);
    await searchConversations({
      q: 'hello world',
      startDate: '2026-01-01T00:00:00.000Z',
      endDate: '2026-02-01T23:59:59.000Z',
      videoId: 'vid-123',
    });
    const url = new URL(calledUrl(), 'http://localhost');
    expect(url.pathname).toBe('/api/conversations/search');
    expect(url.searchParams.get('q')).toBe('hello world');
    expect(url.searchParams.get('start_date')).toBe('2026-01-01T00:00:00.000Z');
    expect(url.searchParams.get('end_date')).toBe('2026-02-01T23:59:59.000Z');
    expect(url.searchParams.get('video_id')).toBe('vid-123');
  });

  it('searchConversations omits falsy filter values', async () => {
    mockJson([]);
    await searchConversations({ q: '', videoId: 'vid-9' });
    const url = new URL(calledUrl(), 'http://localhost');
    expect(url.searchParams.has('q')).toBe(false);
    expect(url.searchParams.get('video_id')).toBe('vid-9');
  });

  it('getConversationVideos hits the videos endpoint with credentials', async () => {
    mockJson([{ video_id: 'v1', video_title: 'Intro' }]);
    const res = await getConversationVideos();
    expect(res).toEqual([{ video_id: 'v1', video_title: 'Intro' }]);
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/conversations/videos');
    expect(init.credentials).toBe('include');
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getConversations } from '../lib/api';

describe('getConversations query-string construction', () => {
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

  it('hits plain /api/conversations with no filters (no "?")', async () => {
    mockJson([]);
    await getConversations();
    expect(calledUrl()).toBe('/api/conversations');
  });

  it('hits plain /api/conversations when filters object is empty', async () => {
    mockJson([]);
    await getConversations({});
    expect(calledUrl()).toBe('/api/conversations');
  });

  it('maps camelCase filters to snake_case params', async () => {
    mockJson([]);
    await getConversations({
      q: 'foo',
      videoId: 'v1',
      dateFrom: '2026-06-01',
      dateTo: '2026-06-07',
    });
    const url = new URL(calledUrl(), 'http://localhost');
    expect(url.pathname).toBe('/api/conversations');
    expect(url.searchParams.get('q')).toBe('foo');
    expect(url.searchParams.get('video_id')).toBe('v1');
    expect(url.searchParams.get('date_from')).toBe('2026-06-01');
    expect(url.searchParams.get('date_to')).toBe('2026-06-07');
  });

  it('omits empty-string filter values', async () => {
    mockJson([]);
    await getConversations({ q: '', videoId: 'v1', dateFrom: '', dateTo: '' });
    const url = new URL(calledUrl(), 'http://localhost');
    expect([...url.searchParams.keys()]).toEqual(['video_id']);
  });

  it('URL-encodes filter values', async () => {
    mockJson([]);
    await getConversations({ q: 'a&b=c' });
    const url = new URL(calledUrl(), 'http://localhost');
    expect(url.searchParams.get('q')).toBe('a&b=c');
  });
});

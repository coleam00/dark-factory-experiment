import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getConversations } from '../lib/api';

describe('getConversations query string (issue #294)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockJson(body: unknown) {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => body,
      text: async () => JSON.stringify(body),
    });
  }

  function calledUrl(): string {
    return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as string;
  }

  it('hits the bare endpoint when no filters are given', async () => {
    mockJson([]);
    await getConversations();
    expect(calledUrl()).toBe('/api/conversations');
  });

  it('hits the bare endpoint when all filter fields are empty', async () => {
    mockJson([]);
    await getConversations({ q: '', date_from: '', date_to: '', video_id: '' });
    expect(calledUrl()).toBe('/api/conversations');
  });

  it('builds a query string from the provided filters and omits empty ones', async () => {
    mockJson([]);
    await getConversations({ date_from: '2026-06-01', video_id: 'vid-1', q: '' });
    const url = calledUrl();
    const [path, qs] = url.split('?');
    expect(path).toBe('/api/conversations');
    const params = new URLSearchParams(qs);
    expect(params.get('date_from')).toBe('2026-06-01');
    expect(params.get('video_id')).toBe('vid-1');
    expect(params.has('q')).toBe(false);
    expect(params.has('date_to')).toBe(false);
  });

  it('encodes all four filters together', async () => {
    mockJson([]);
    await getConversations({
      q: 'rag & retrieval',
      date_from: '2026-05-01',
      date_to: '2026-05-31',
      video_id: 'vid-1',
    });
    const params = new URLSearchParams(calledUrl().split('?')[1]);
    expect(params.get('q')).toBe('rag & retrieval');
    expect(params.get('date_from')).toBe('2026-05-01');
    expect(params.get('date_to')).toBe('2026-05-31');
    expect(params.get('video_id')).toBe('vid-1');
  });
});

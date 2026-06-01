import { afterEach, describe, expect, it, vi } from 'vitest';
import { getConversations } from '../lib/api';

function mockFetch() {
  const fn = vi.fn(
    async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('getConversations query-string serialization (issue #294)', () => {
  it('hits the bare endpoint when no filters are given', async () => {
    const fetchFn = mockFetch();
    await getConversations();
    expect(fetchFn).toHaveBeenCalledWith('/api/conversations', expect.anything());
  });

  it('maps camelCase filters to snake_case query params', async () => {
    const fetchFn = mockFetch();
    await getConversations({
      q: 'rag',
      startDate: '2026-01-01T00:00:00.000Z',
      endDate: '2026-02-01T23:59:59.999Z',
      videoId: 'vid-1',
    });
    const url = (fetchFn.mock.calls[0]?.[0] ?? '') as string;
    expect(url.startsWith('/api/conversations?')).toBe(true);
    const qs = new URLSearchParams(url.split('?')[1]);
    expect(qs.get('q')).toBe('rag');
    expect(qs.get('start_date')).toBe('2026-01-01T00:00:00.000Z');
    expect(qs.get('end_date')).toBe('2026-02-01T23:59:59.999Z');
    expect(qs.get('video_id')).toBe('vid-1');
  });

  it('omits empty / undefined filter values', async () => {
    const fetchFn = mockFetch();
    await getConversations({ videoId: 'only-this' });
    const url = (fetchFn.mock.calls[0]?.[0] ?? '') as string;
    const qs = new URLSearchParams(url.split('?')[1]);
    expect(qs.get('video_id')).toBe('only-this');
    expect(qs.has('q')).toBe(false);
    expect(qs.has('start_date')).toBe(false);
    expect(qs.has('end_date')).toBe(false);
  });
});

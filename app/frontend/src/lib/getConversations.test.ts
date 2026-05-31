import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { type ConversationFilters, getConversations } from './api';

describe('getConversations query string', () => {
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

  it('hits bare /conversations when no filters given', async () => {
    mockJson([]);
    await getConversations();
    expect(calledUrl()).toBe('/api/conversations');
  });

  it('hits bare /conversations for an empty filters object', async () => {
    mockJson([]);
    await getConversations({});
    expect(calledUrl()).toBe('/api/conversations');
  });

  it('serializes only the provided filter keys', async () => {
    mockJson([]);
    await getConversations({ startDate: '2026-01-01T00:00:00.000Z' });
    expect(calledUrl()).toBe('/api/conversations?start_date=2026-01-01T00%3A00%3A00.000Z');
  });

  it('serializes all three filters with snake_case keys', async () => {
    mockJson([]);
    const filters: ConversationFilters = {
      startDate: '2026-01-01T00:00:00.000Z',
      endDate: '2026-02-01T23:59:59.999Z',
      videoId: 'vid-123',
    };
    await getConversations(filters);
    const url = calledUrl();
    const qs = new URLSearchParams(url.split('?')[1]);
    expect(qs.get('start_date')).toBe('2026-01-01T00:00:00.000Z');
    expect(qs.get('end_date')).toBe('2026-02-01T23:59:59.999Z');
    expect(qs.get('video_id')).toBe('vid-123');
  });

  it('omits empty-string filter values', async () => {
    mockJson([]);
    await getConversations({ startDate: '', endDate: '', videoId: 'vid-9' });
    expect(calledUrl()).toBe('/api/conversations?video_id=vid-9');
  });
});

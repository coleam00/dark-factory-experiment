import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createShareLink, getSharedConversation, revokeShareLink } from '../lib/api';

describe('share API wrappers', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('createShareLink POSTs to /conversations/:id/share with credentials', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ token: 'abc123', url_path: '/share/abc123' }),
      text: async () => '',
    });

    const res = await createShareLink('conv-1');
    expect(res).toEqual({ token: 'abc123', url_path: '/share/abc123' });
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/conversations/conv-1/share');
    expect(opts?.method).toBe('POST');
    expect(opts?.credentials).toBe('include');
  });

  it('revokeShareLink DELETEs with credentials', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 204,
      text: async () => '',
    });

    await revokeShareLink('conv-1');
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/conversations/conv-1/share');
    expect(opts?.method).toBe('DELETE');
    expect(opts?.credentials).toBe('include');
  });

  it('getSharedConversation fetches /api/share/:token without credentials', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        title: 'Test conv',
        messages: [{ id: 'm1', role: 'user', content: 'hi', sources: null }],
      }),
      text: async () => '',
    });

    const res = await getSharedConversation('tok-1');
    expect(res.title).toBe('Test conv');
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, opts] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/share/tok-1');
    // Must not include credentials so anon readers aren't bounced to login
    expect(opts?.credentials).toBeUndefined();
  });

  it('getSharedConversation throws ApiError on 404', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () => JSON.stringify({ detail: 'Share link not found' }),
    });

    await expect(getSharedConversation('bad-tok')).rejects.toBeInstanceOf(ApiError);
    try {
      await getSharedConversation('bad-tok');
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(404);
    }
  });
});

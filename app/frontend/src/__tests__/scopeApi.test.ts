import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { updateConversationScope } from '../lib/api';

describe('updateConversationScope', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockOkJson(body: unknown) {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    });
  }

  it('PATCHes /api/conversations/{id}/scope with the selected video ids', async () => {
    mockOkJson({ id: 'c1', title: 'T', scoped_video_ids: ['v1', 'v2'] });

    const conv = await updateConversationScope('c1', ['v1', 'v2']);

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, options] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/conversations/c1/scope');
    expect(options.method).toBe('PATCH');
    expect(JSON.parse(options.body as string)).toEqual({ video_ids: ['v1', 'v2'] });
    expect(conv.scoped_video_ids).toEqual(['v1', 'v2']);
  });

  it('sends null video_ids to clear the scope', async () => {
    mockOkJson({ id: 'c1', title: 'T', scoped_video_ids: null });

    const conv = await updateConversationScope('c1', null);

    const [, options] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(options.body as string)).toEqual({ video_ids: null });
    expect(conv.scoped_video_ids).toBeNull();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { addVideoByUrl, deleteVideo, listAdminVideos, resyncVideo, syncChannel } from '../lib/api';

describe('admin api wrappers', () => {
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

  it('listAdminVideos hits GET /api/admin/videos with credentials', async () => {
    mockJson({ videos: [] });
    const res = await listAdminVideos();
    expect(res.videos).toEqual([]);
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/admin/videos');
    expect(init.credentials).toBe('include');
  });

  it('addVideoByUrl POSTs the url JSON', async () => {
    mockJson({ video_id: 'v1', chunks_created: 3, status: 'ok' }, 201);
    const res = await addVideoByUrl('https://www.youtube.com/watch?v=x');
    expect(res.chunks_created).toBe(3);
    const [, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ url: 'https://www.youtube.com/watch?v=x' });
  });

  it('deleteVideo issues DELETE and resolves on 204', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 204,
      statusText: 'No Content',
      text: async () => '',
    });
    await expect(deleteVideo('abc')).resolves.toBeUndefined();
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/admin/videos/abc');
    expect(init.method).toBe('DELETE');
  });

  it('deleteVideo throws on non-2xx', async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      text: async () => '{"detail":"Video not found"}',
    });
    await expect(deleteVideo('abc')).rejects.toThrow(/404/);
  });

  it('resyncVideo posts to re-sync endpoint', async () => {
    mockJson({ video_id: 'v1', chunks_created: 5, status: 'ok' });
    const res = await resyncVideo('v1');
    expect(res.chunks_created).toBe(5);
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/admin/videos/v1/re-sync');
    expect(init.method).toBe('POST');
  });

  it('syncChannel posts to sync-channel endpoint', async () => {
    mockJson({
      sync_run_id: 's1',
      status: 'completed',
      videos_total: 3,
      videos_new: 1,
      videos_error: 0,
    });
    const res = await syncChannel();
    expect(res.status).toBe('completed');
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/admin/videos/sync-channel');
    expect(init.method).toBe('POST');
  });
});

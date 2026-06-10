import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createConversation, setConversationScope } from './api';

function okResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('conversation scope api wrappers', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockResolvedValue(okResponse({ id: 'c1' }));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it('createConversation() sends an empty body (unscoped)', async () => {
    await createConversation();
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/conversations');
    expect(options.body).toBe('{}');
  });

  it('createConversation([]) sends an empty body (unscoped)', async () => {
    await createConversation([]);
    const [, options] = fetchMock.mock.calls[0];
    expect(options.body).toBe('{}');
  });

  it('createConversation with ids sends video_ids', async () => {
    await createConversation(['v1', 'v2']);
    const [, options] = fetchMock.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual({ video_ids: ['v1', 'v2'] });
  });

  it('setConversationScope posts video_ids to the scope endpoint', async () => {
    await setConversationScope('c1', ['v1']);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/conversations/c1/scope');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ video_ids: ['v1'] });
  });
});

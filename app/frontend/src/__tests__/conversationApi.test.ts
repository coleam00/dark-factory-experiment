import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createConversation } from '../lib/api';

// issue #279: createConversation optionally scopes the new conversation to a
// subset of videos. A non-empty list is sent as { video_ids: [...] }; no
// selection (or an empty list) sends an empty body so the conversation stays
// unscoped (searches the whole library).
describe('createConversation video scope (issue #279)', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 201,
        statusText: 'Created',
        json: async () => ({
          id: 'c1',
          title: 'New Conversation',
          created_at: '',
          updated_at: '',
        }),
        text: async () => '{}',
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function lastBody(): unknown {
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
    const init = calls[calls.length - 1][1] as RequestInit;
    return JSON.parse(init.body as string);
  }

  it('sends video_ids when a non-empty scope is provided', async () => {
    await createConversation(['v1', 'v2']);
    expect(lastBody()).toEqual({ video_ids: ['v1', 'v2'] });
  });

  it('sends an empty body when no scope is provided', async () => {
    await createConversation();
    expect(lastBody()).toEqual({});
  });

  it('sends an empty body when an empty scope is provided', async () => {
    await createConversation([]);
    expect(lastBody()).toEqual({});
  });
});

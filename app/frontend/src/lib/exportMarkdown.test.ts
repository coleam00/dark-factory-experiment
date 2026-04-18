import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Conversation, Message } from './api';

vi.mock('file-saver', () => ({ saveAs: vi.fn() }));

import { saveAs } from 'file-saver';
import { exportConversationAsMarkdown } from './exportMarkdown';

describe('exportConversationAsMarkdown', () => {
  beforeEach(() => {
    vi.mocked(saveAs).mockClear();
  });

  it('should format header with title and ISO timestamp', async () => {
    const conv: Conversation = { id: '1', title: 'Test Chat', created_at: '', updated_at: '' };
    const messages: Message[] = [];

    exportConversationAsMarkdown(conv, messages);

    const blob = vi.mocked(saveAs).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toMatch(/^# Test Chat\n\n\d{4}-\d{2}-\d{2}T/);
  });

  it('should map user role to **You:** and assistant to **Assistant:**', async () => {
    const conv: Conversation = { id: '1', title: 'Chat', created_at: '', updated_at: '' };
    const messages: Message[] = [
      { id: '1', conversation_id: '1', role: 'user', content: 'Hello', created_at: '' },
      { id: '2', conversation_id: '1', role: 'assistant', content: 'Hi there', created_at: '' },
    ];

    exportConversationAsMarkdown(conv, messages);

    const blob = vi.mocked(saveAs).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toContain('**You:** Hello');
    expect(text).toContain('**Assistant:** Hi there');
  });

  it('should generate valid filename slug', () => {
    const conv: Conversation = {
      id: '1',
      title: '  My Video: Episode 1  ',
      created_at: '',
      updated_at: '',
    };
    const messages: Message[] = [];

    exportConversationAsMarkdown(conv, messages);

    const filename = vi.mocked(saveAs).mock.calls[0][1];
    expect(filename).toMatch(/^conversation-my-video-episode-1-\d{4}-\d{2}-\d{2}\.md$/);
  });

  it('should handle empty messages array', async () => {
    const conv: Conversation = { id: '1', title: 'Empty Chat', created_at: '', updated_at: '' };

    exportConversationAsMarkdown(conv, []);

    const blob = vi.mocked(saveAs).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toContain('# Empty Chat');
    expect(text).toContain('---');
  });

  it('should handle special markdown characters in content', async () => {
    const conv: Conversation = { id: '1', title: 'Chat', created_at: '', updated_at: '' };
    const messages: Message[] = [
      { id: '1', conversation_id: '1', role: 'user', content: '# Warning', created_at: '' },
    ];

    exportConversationAsMarkdown(conv, messages);

    const blob = vi.mocked(saveAs).mock.calls[0][0] as Blob;
    const text = await blob.text();
    expect(text).toContain('**You:** # Warning');
  });
});

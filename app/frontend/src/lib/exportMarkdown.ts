/**
 * Exports a conversation and its messages as a Markdown file and triggers a browser download.
 *
 * @param conversation - The conversation object (used for title and metadata)
 * @param messages - The list of messages to render in the export
 *
 * @example
 * exportConversationAsMarkdown(conversation, messages);
 * // Downloads: conversation-<slug>-<date>.md
 */
import { saveAs } from 'file-saver';
import type { Conversation, Message } from './api';

export function exportConversationAsMarkdown(
  conversation: Conversation,
  messages: Message[],
): void {
  const header = `# ${conversation.title}\n\n${new Date().toISOString()}\n\n---\n\n`;
  const body = messages
    .map((msg) => {
      const role = msg.role === 'user' ? '**You:**' : '**Assistant:**';
      return `${role} ${msg.content}\n\n`;
    })
    .join('');
  const slug = conversation.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  const date = new Date().toISOString().split('T')[0];
  const filename = `conversation-${slug}-${date}.md`;
  const blob = new Blob([header + body], { type: 'text/markdown;charset=utf-8' });
  saveAs(blob, filename);
}

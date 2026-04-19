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
import type { Citation, Conversation, Message } from './api';

function formatTimestamp(seconds: number): string {
  const s = Math.floor(seconds);
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatCitation(citation: Citation): string {
  const videoId = (() => {
    try {
      return new URL(citation.video_url).searchParams.get('v') ?? '';
    } catch {
      return '';
    }
  })();
  const externalUrl = videoId
    ? `https://www.youtube.com/watch?v=${videoId}&t=${Math.floor(citation.start_seconds)}s`
    : '';
  const start = formatTimestamp(citation.start_seconds);
  const end = formatTimestamp(citation.end_seconds);
  const link = externalUrl ? `[${citation.video_title}](${externalUrl})` : citation.video_title;
  return `- ${link} — ${start}–${end}\n  > "${citation.snippet}"`;
}

function formatSources(sources: Citation[]): string {
  if (!sources || sources.length === 0) return '';
  return `\n\n**Sources:**\n${sources.map(formatCitation).join('\n')}`;
}

export function exportConversationAsMarkdown(
  conversation: Conversation,
  messages: Message[],
): void {
  const header = `# ${conversation.title}\n\n${new Date().toISOString()}\n\n---\n\n`;
  const body = messages
    .map((msg) => {
      const role = msg.role === 'user' ? '**You:**' : '**Assistant:**';
      const sources = msg.sources ? formatSources(msg.sources) : '';
      return `${role} ${msg.content}${sources}\n\n`;
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

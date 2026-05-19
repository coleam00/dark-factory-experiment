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

export function formatTimestamp(seconds: number): string {
  const s = Math.floor(seconds);
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

export function formatCitation(citation: Citation): string {
  if (!citation.snippet?.trim()) return '';

  const range = `${formatTimestamp(citation.start_seconds)}–${formatTimestamp(citation.end_seconds)}`;

  // Issue #147: Dynamous citations link to the Circle lesson_url. Circle
  // doesn't support timestamp deep-links, so the (MM:SS–MM:SS) range is
  // shown as text only and the URL points to the lesson root.
  if (citation.source_type === 'dynamous') {
    const lessonUrl = citation.lesson_url?.trim();
    if (!lessonUrl) {
      return `${citation.video_title} — ${range}\n  > "${citation.snippet}"`;
    }
    const link = `[${citation.video_title}](${lessonUrl})`;
    return `- ${link} — ${range}\n  > "${citation.snippet}"`;
  }

  // Default / 'youtube': embedded player + ?t= deep-link.
  let videoId: string;
  try {
    videoId = new URL(citation.video_url).searchParams.get('v') ?? '';
  } catch {
    console.warn(
      `[exportMarkdown] Skipping timestamp link — invalid video_url: "${citation.video_url}"`,
    );
    return `${citation.video_title} (timestamp link unavailable) — ${range}`;
  }

  if (!videoId) {
    console.warn(
      `[exportMarkdown] Skipping timestamp link — invalid video_url: "${citation.video_url}"`,
    );
    return `${citation.video_title} (timestamp link unavailable) — ${range}`;
  }

  const externalUrl = `https://www.youtube.com/watch?v=${videoId}&t=${Math.floor(citation.start_seconds)}s`;
  const link = `[${citation.video_title}](${externalUrl})`;
  return `- ${link} — ${range}\n  > "${citation.snippet}"`;
}

export function formatSources(sources: Citation[]): string {
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

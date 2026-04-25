import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Citation } from '../lib/api';
import { CitationModal, formatTimestamp } from './CitationModal';

const mockCitation: Citation = {
  chunk_id: 'chunk-1',
  video_id: 'vid-1',
  video_title: 'Test Video Title',
  video_url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
  start_seconds: 754,
  end_seconds: 762,
  snippet: 'This is a sample transcript snippet that appears in the video at the cited moment.',
};

describe('CitationModal', () => {
  it('renders the modal with citation data', () => {
    const onClose = vi.fn();
    render(<CitationModal citation={mockCitation} onClose={onClose} />);

    expect(screen.getByText('Test Video Title')).toBeInTheDocument();
    expect(screen.getByText(/at 12:34/)).toBeInTheDocument();
  });

  it('shows correct iframe src with start param', () => {
    const onClose = vi.fn();
    render(<CitationModal citation={mockCitation} onClose={onClose} />);

    const iframe = screen.getByTitle('YouTube video player') as HTMLIFrameElement;
    expect(iframe.src).toContain('youtube.com/embed/dQw4w9WgXcQ');
    expect(iframe.src).toContain('start=754');
    expect(iframe.src).toContain('autoplay=1');
  });

  it('shows transcript snippet with heading', () => {
    const onClose = vi.fn();
    render(<CitationModal citation={mockCitation} onClose={onClose} />);

    expect(screen.getByText('Transcript Excerpt')).toBeInTheDocument();
    expect(
      screen.getByText(
        'This is a sample transcript snippet that appears in the video at the cited moment.',
      ),
    ).toBeInTheDocument();
  });

  it('shows external link with correct t param', () => {
    const onClose = vi.fn();
    render(<CitationModal citation={mockCitation} onClose={onClose} />);

    const link = screen.getByRole('link', { name: /Open on YouTube/i }) as HTMLAnchorElement;
    expect(link.href).toContain('youtube.com/watch?v=dQw4w9WgXcQ');
    expect(link.href).toContain('t=754s');
  });

  it('closes modal on ESC key', () => {
    const onClose = vi.fn();
    render(<CitationModal citation={mockCitation} onClose={onClose} />);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes modal on backdrop click', () => {
    const onClose = vi.fn();
    render(<CitationModal citation={mockCitation} onClose={onClose} />);

    // Click on the backdrop (outer div with fixed inset-0)
    const backdrop = document.querySelector('.fixed.inset-0.bg-black\\/60');
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop as Element);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes modal on close button click', () => {
    const onClose = vi.fn();
    render(<CitationModal citation={mockCitation} onClose={onClose} />);

    const closeButton = screen.getByRole('button', { name: 'Close' });
    fireEvent.click(closeButton);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('truncates long snippets with ellipsis', () => {
    const longCitation: Citation = {
      ...mockCitation,
      snippet: 'A'.repeat(400),
    };
    const onClose = vi.fn();
    render(<CitationModal citation={longCitation} onClose={onClose} />);

    const snippetEl = screen.getByText(/^A{297}…$/);
    expect(snippetEl).toBeInTheDocument();
  });

  it('shows video unavailable when video URL is invalid', () => {
    const badCitation: Citation = {
      ...mockCitation,
      video_url: 'not-a-valid-url',
    };
    const onClose = vi.fn();
    render(<CitationModal citation={badCitation} onClose={onClose} />);

    expect(screen.getByText('Video unavailable')).toBeInTheDocument();
  });

  it('shows video unavailable when youtube URL has empty v parameter', () => {
    const badCitation: Citation = {
      ...mockCitation,
      video_url: 'https://youtube.com/watch?v=',
    };
    const onClose = vi.fn();
    render(<CitationModal citation={badCitation} onClose={onClose} />);

    expect(screen.getByText('Video unavailable')).toBeInTheDocument();
  });

  it('shows iframe when youtube URL has a valid v parameter with extra query params', () => {
    const badCitation: Citation = {
      ...mockCitation,
      video_url: 'https://youtube.com/watch?v=abc123&other=param',
    };
    const onClose = vi.fn();
    render(<CitationModal citation={badCitation} onClose={onClose} />);

    const iframe = screen.queryByTitle('YouTube video player');
    expect(iframe).toBeInTheDocument();
  });

  it('locks body scroll while mounted and restores on unmount', () => {
    const onClose = vi.fn();
    const { unmount } = render(<CitationModal citation={mockCitation} onClose={onClose} />);
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).toBe('');
  });

  it('handles relative URL gracefully', () => {
    const badCitation: Citation = {
      ...mockCitation,
      video_url: '/watch?v=abc123',
    };
    const onClose = vi.fn();
    render(<CitationModal citation={badCitation} onClose={onClose} />);

    // Relative URL throws in URL constructor, shows unavailable
    expect(screen.getByText('Video unavailable')).toBeInTheDocument();
  });
});

describe('formatTimestamp', () => {
  it('formats 0 seconds as 0:00', () => {
    expect(formatTimestamp(0)).toBe('0:00');
  });

  it('formats 60 seconds as 1:00', () => {
    expect(formatTimestamp(60)).toBe('1:00');
  });

  it('formats >1 hour as minutes:seconds (no hour pad)', () => {
    // Documents current behavior: 3661s -> 61:01
    expect(formatTimestamp(3661)).toBe('61:01');
  });

  it('formats fractional seconds by flooring', () => {
    expect(formatTimestamp(12.7)).toBe('0:12');
  });
});

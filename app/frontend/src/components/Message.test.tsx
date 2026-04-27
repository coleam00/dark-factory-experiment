import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Citation } from '../lib/api';
import { Message } from './Message';

describe('Message — streamingStatus rendering', () => {
  it('renders Searching indicator with subject when isStreaming, no content, status set', () => {
    render(
      <Message
        role="assistant"
        content=""
        isStreaming={true}
        streamingStatus={{ tool: 'search_videos', subject: 'building agents' }}
      />,
    );
    expect(screen.getByText('Searching: building agents…')).toBeInTheDocument();
    expect(screen.queryByText('Working…')).not.toBeInTheDocument();
  });

  it('renders "Working…" fallback when isStreaming, no content, subject is empty', () => {
    render(
      <Message
        role="assistant"
        content=""
        isStreaming={true}
        streamingStatus={{ tool: 'unknown_tool', subject: '' }}
      />,
    );
    expect(screen.getByText('Working…')).toBeInTheDocument();
    expect(screen.queryByText(/Searching/)).not.toBeInTheDocument();
  });

  it('renders TypingIndicator when isStreaming, no content, no streamingStatus', () => {
    render(<Message role="assistant" content="" isStreaming={true} streamingStatus={null} />);
    expect(screen.queryByText(/Searching/)).not.toBeInTheDocument();
    expect(screen.queryByText('Working…')).not.toBeInTheDocument();
    // TypingIndicator renders 3 typing-dot divs
    const dots = document.querySelectorAll('.typing-dot');
    expect(dots).toHaveLength(3);
  });

  it('renders content instead of status indicator when content is present', () => {
    render(
      <Message
        role="assistant"
        content="Answer here."
        isStreaming={true}
        streamingStatus={{ tool: 'search_videos', subject: 'building agents' }}
      />,
    );
    expect(screen.getByText('Answer here.')).toBeInTheDocument();
    expect(screen.queryByText(/Searching/)).not.toBeInTheDocument();
  });
});

describe('Message — citation chip segment_count label', () => {
  const baseCitation: Citation = {
    chunk_id: 'c1',
    video_id: 'v1',
    video_title: 'Demo Video',
    video_url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    start_seconds: 60,
    end_seconds: 70,
    snippet: 'snippet text',
    is_cited: true,
  };

  it('shows "(N segments)" suffix when segment_count > 1', () => {
    const citation = { ...baseCitation, segment_count: 4 };
    render(
      <Message
        role="assistant"
        content="Answer text."
        isStreaming={false}
        streamingStatus={null}
        sources={[citation]}
        onCitationClick={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /\(4 segments\)/ })).toBeInTheDocument();
  });

  it('omits segment label when segment_count is 1', () => {
    const citation = { ...baseCitation, segment_count: 1 };
    render(
      <Message
        role="assistant"
        content="Answer text."
        isStreaming={false}
        streamingStatus={null}
        sources={[citation]}
        onCitationClick={vi.fn()}
      />,
    );
    expect(screen.queryByText(/segments/)).not.toBeInTheDocument();
  });

  it('omits segment label when segment_count is absent', () => {
    render(
      <Message
        role="assistant"
        content="Answer text."
        isStreaming={false}
        streamingStatus={null}
        sources={[baseCitation]}
        onCitationClick={vi.fn()}
      />,
    );
    expect(screen.queryByText(/segments/)).not.toBeInTheDocument();
  });
});

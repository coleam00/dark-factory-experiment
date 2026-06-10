import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Video } from '../lib/api';
import { VideoScopePicker } from './VideoScopePicker';

const mockVideos: Video[] = [
  {
    id: 'v1',
    title: 'Intro to RAG',
    description: '',
    url: 'https://youtu.be/a',
    created_at: '2024-01-01T00:00:00Z',
    channel_title: 'Cole Medin',
  },
  {
    id: 'v2',
    title: 'Agents Deep Dive',
    description: '',
    url: 'https://youtu.be/b',
    created_at: '2024-01-02T00:00:00Z',
    channel_title: 'Cole Medin',
  },
];

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api');
  return {
    ...actual,
    getVideos: vi.fn(),
  };
});

import { getVideos } from '../lib/api';

describe('VideoScopePicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getVideos).mockResolvedValue(mockVideos);
  });

  it('renders nothing when closed', () => {
    const { container } = render(
      <VideoScopePicker open={false} onConfirm={vi.fn()} onClose={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
    expect(getVideos).not.toHaveBeenCalled();
  });

  it('loads and renders the fetched videos when open', async () => {
    render(<VideoScopePicker open={true} onConfirm={vi.fn()} onClose={vi.fn()} />);

    expect(await screen.findByText('Intro to RAG')).toBeInTheDocument();
    expect(screen.getByText('Agents Deep Dive')).toBeInTheDocument();
    expect(getVideos).toHaveBeenCalledTimes(1);
  });

  it('disables Confirm at zero selection and enables after toggling', async () => {
    render(<VideoScopePicker open={true} onConfirm={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText('Intro to RAG');

    const confirm = screen.getByRole('button', { name: /^Focus$/ });
    expect(confirm).toBeDisabled();

    fireEvent.click(screen.getByText('Intro to RAG'));
    expect(screen.getByRole('button', { name: /Focus on 1 video$/ })).toBeEnabled();
  });

  it('fires onConfirm with the selected ids', async () => {
    const onConfirm = vi.fn();
    render(<VideoScopePicker open={true} onConfirm={onConfirm} onClose={vi.fn()} />);
    await screen.findByText('Intro to RAG');

    fireEvent.click(screen.getByText('Intro to RAG'));
    fireEvent.click(screen.getByText('Agents Deep Dive'));
    fireEvent.click(screen.getByRole('button', { name: /Focus on 2 videos/ }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0].sort()).toEqual(['v1', 'v2']);
  });

  it('unchecking removes the id from the selection', async () => {
    const onConfirm = vi.fn();
    render(<VideoScopePicker open={true} onConfirm={onConfirm} onClose={vi.fn()} />);
    await screen.findByText('Intro to RAG');

    fireEvent.click(screen.getByText('Intro to RAG'));
    fireEvent.click(screen.getByText('Agents Deep Dive'));
    fireEvent.click(screen.getByText('Intro to RAG')); // toggle off
    fireEvent.click(screen.getByRole('button', { name: /Focus on 1 video$/ }));

    expect(onConfirm).toHaveBeenCalledWith(['v2']);
  });

  it('Clear selection resets to zero and disables Confirm', async () => {
    render(<VideoScopePicker open={true} onConfirm={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText('Intro to RAG');

    fireEvent.click(screen.getByText('Intro to RAG'));
    fireEvent.click(screen.getByRole('button', { name: /Clear selection/ }));

    expect(screen.getByRole('button', { name: /^Focus$/ })).toBeDisabled();
  });

  it('filters the list by title', async () => {
    render(<VideoScopePicker open={true} onConfirm={vi.fn()} onClose={vi.fn()} />);
    await screen.findByText('Intro to RAG');

    fireEvent.change(screen.getByPlaceholderText('Filter videos…'), {
      target: { value: 'agents' },
    });

    expect(screen.queryByText('Intro to RAG')).not.toBeInTheDocument();
    expect(screen.getByText('Agents Deep Dive')).toBeInTheDocument();
  });

  it('pre-checks initialSelected ids', async () => {
    render(
      <VideoScopePicker
        open={true}
        initialSelected={['v2']}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    await screen.findByText('Agents Deep Dive');

    expect(screen.getByRole('button', { name: /Focus on 1 video$/ })).toBeEnabled();
  });

  it('shows an error message when loading fails', async () => {
    vi.mocked(getVideos).mockRejectedValueOnce(new Error('boom'));
    render(<VideoScopePicker open={true} onConfirm={vi.fn()} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('boom'));
  });
});

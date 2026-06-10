import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../lib/api';
import { VideoScopePicker } from './VideoScopePicker';

const VIDEOS: api.Video[] = [
  {
    id: 'v1',
    title: 'Intro to RAG',
    description: '',
    url: 'https://youtube.com/watch?v=a',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'v2',
    title: 'Agent Teams',
    description: '',
    url: 'https://youtube.com/watch?v=b',
    created_at: '2026-01-02T00:00:00Z',
  },
];

describe('VideoScopePicker', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'getVideos').mockResolvedValue(VIDEOS);
  });

  it('renders the fetched videos as checkboxes', async () => {
    render(
      <VideoScopePicker conversationId="c1" current={null} onClose={vi.fn()} onSaved={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByText('Intro to RAG')).toBeInTheDocument();
      expect(screen.getByText('Agent Teams')).toBeInTheDocument();
    });
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
  });

  it('pre-checks the videos in the current scope', async () => {
    render(
      <VideoScopePicker conversationId="c1" current={['v2']} onClose={vi.fn()} onSaved={vi.fn()} />,
    );

    await waitFor(() => expect(screen.getByText('Agent Teams')).toBeInTheDocument());
    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    expect(checkboxes[0].checked).toBe(false); // v1
    expect(checkboxes[1].checked).toBe(true); // v2
  });

  it('toggling and applying saves the selected ids and reports them via onSaved', async () => {
    const saveSpy = vi.spyOn(api, 'updateConversationScope').mockResolvedValue({
      id: 'c1',
      title: 'T',
      created_at: '',
      updated_at: '',
      scoped_video_ids: ['v1'],
    });
    const onSaved = vi.fn();
    const onClose = vi.fn();

    render(
      <VideoScopePicker conversationId="c1" current={null} onClose={onClose} onSaved={onSaved} />,
    );

    await waitFor(() => expect(screen.getByText('Intro to RAG')).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole('checkbox')[0]); // select v1
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => {
      expect(saveSpy).toHaveBeenCalledWith('c1', ['v1']);
      expect(onSaved).toHaveBeenCalledWith(['v1']);
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('"Search all videos" clears the scope (null)', async () => {
    const saveSpy = vi.spyOn(api, 'updateConversationScope').mockResolvedValue({
      id: 'c1',
      title: 'T',
      created_at: '',
      updated_at: '',
      scoped_video_ids: null,
    });
    const onSaved = vi.fn();

    render(
      <VideoScopePicker conversationId="c1" current={['v1']} onClose={vi.fn()} onSaved={onSaved} />,
    );

    await waitFor(() => expect(screen.getByText('Intro to RAG')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Search all videos' }));

    await waitFor(() => {
      expect(saveSpy).toHaveBeenCalledWith('c1', null);
      expect(onSaved).toHaveBeenCalledWith(null);
    });
  });

  it('applying with nothing selected clears the scope instead of scoping to nothing', async () => {
    const saveSpy = vi.spyOn(api, 'updateConversationScope').mockResolvedValue({
      id: 'c1',
      title: 'T',
      created_at: '',
      updated_at: '',
      scoped_video_ids: null,
    });

    render(
      <VideoScopePicker conversationId="c1" current={null} onClose={vi.fn()} onSaved={vi.fn()} />,
    );

    await waitFor(() => expect(screen.getByText('Intro to RAG')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => {
      expect(saveSpy).toHaveBeenCalledWith('c1', null);
    });
  });
});

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { VideoScopePicker } from '../components/VideoScopePicker';
import * as api from '../lib/api';

const VIDEOS: api.Video[] = [
  {
    id: 'v1',
    title: 'First Video',
    description: '',
    url: 'https://youtu.be/1',
    created_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 'v2',
    title: 'Second Video',
    description: '',
    url: 'https://youtu.be/2',
    created_at: '2024-01-01T00:00:00Z',
  },
];

describe('VideoScopePicker', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getVideos').mockResolvedValue(VIDEOS);
    vi.spyOn(api, 'createConversation').mockResolvedValue({
      id: 'c-new',
      title: 'New Conversation',
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
      scoped_video_ids: null,
    });
  });

  it('creates a scoped conversation with the selected video ids', async () => {
    const onClose = vi.fn();
    const onCreated = vi.fn();
    render(<VideoScopePicker isOpen={true} onClose={onClose} onCreated={onCreated} />);

    // Wait for videos to load.
    const first = await screen.findByText('First Video');
    fireEvent.click(first);

    fireEvent.click(screen.getByRole('button', { name: /start chat/i }));

    await waitFor(() => expect(api.createConversation).toHaveBeenCalledTimes(1));
    expect(api.createConversation).toHaveBeenCalledWith(['v1']);
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });

  it('creates an unscoped conversation when nothing is selected', async () => {
    const onClose = vi.fn();
    const onCreated = vi.fn();
    render(<VideoScopePicker isOpen={true} onClose={onClose} onCreated={onCreated} />);

    await screen.findByText('First Video');
    // Default summary indicates search-all.
    expect(screen.getByTestId('scope-summary').textContent).toMatch(/all videos/i);

    fireEvent.click(screen.getByRole('button', { name: /start chat/i }));

    await waitFor(() => expect(api.createConversation).toHaveBeenCalledTimes(1));
    // No ids passed → unscoped (search everything).
    expect(api.createConversation).toHaveBeenCalledWith(undefined);
  });

  it('renders nothing when closed', () => {
    const { container } = render(
      <VideoScopePicker isOpen={false} onClose={vi.fn()} onCreated={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(api.getVideos).not.toHaveBeenCalled();
  });
});

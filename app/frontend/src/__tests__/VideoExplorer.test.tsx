import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { VideoExplorer } from '../components/VideoExplorer';
import * as api from '../lib/api';

// Default auth mock: admin user so the "+ Add Video" button renders. The
// non-admin gate is covered by its own suite below that re-mocks useAuth.
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: {
      id: 'test-admin',
      email: 'admin@test',
      is_admin: true,
      messages_used_today: 0,
      messages_remaining_today: 100,
      rate_window_resets_at: null,
    },
    status: 'authed',
    refresh: vi.fn(),
  }),
}));

describe('VideoExplorer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Set default mocks
    vi.spyOn(api, 'getVideos').mockResolvedValue([]);
    vi.spyOn(api, 'ingestVideo').mockResolvedValue({
      video_id: 'new',
      chunks_created: 5,
      status: 'created',
    });
  });

  describe('handleIngest', () => {
    it('shows validation error when fields are empty', async () => {
      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      // Open the ingest dialog
      const addButton = screen.getByText('+ Add Video');
      fireEvent.click(addButton);

      // Try to submit empty form
      const addVideoButton = screen.getByRole('button', { name: 'Add Video' });
      fireEvent.click(addVideoButton);

      // Should show validation error
      expect(screen.getByText('All fields are required.')).toBeInTheDocument();
    });

    it('shows validation error when URL is missing', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Test Video',
          description: 'A test video description',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]);

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      // Wait for initial load
      await waitFor(() => {
        expect(screen.getByText('Test Video')).toBeInTheDocument();
      });

      // Open the ingest dialog
      const addButton = screen.getByText('+ Add Video');
      fireEvent.click(addButton);

      // Fill partial form (missing URL and transcript)
      fireEvent.change(screen.getByLabelText('Title'), {
        target: { value: 'Test Title' },
      });
      fireEvent.change(screen.getByLabelText('Description'), {
        target: { value: 'Test Description' },
      });

      // Try to submit
      const addVideoButton = screen.getByRole('button', { name: 'Add Video' });
      fireEvent.click(addVideoButton);

      expect(screen.getByText('All fields are required.')).toBeInTheDocument();
    });

    it('successfully ingests video and refreshes video list', async () => {
      const initialVideos = [
        {
          id: '1',
          title: 'Test Video',
          description: 'A test video description',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
        },
      ];

      const updatedVideos = [
        ...initialVideos,
        {
          id: '2',
          title: 'New Video',
          description: 'New description',
          url: 'https://youtube.com/watch?v=xyz789',
          created_at: '2024-01-02T00:00:00Z',
        },
      ];

      vi.spyOn(api, 'getVideos')
        .mockResolvedValueOnce(initialVideos as api.Video[])
        .mockResolvedValueOnce(updatedVideos as api.Video[]);
      vi.spyOn(api, 'ingestVideo').mockResolvedValueOnce({
        video_id: '2',
        chunks_created: 10,
        status: 'created',
      });

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      // Wait for initial load
      await waitFor(() => {
        expect(screen.getByText('Test Video')).toBeInTheDocument();
      });

      // Open the ingest dialog
      const addButton = screen.getByText('+ Add Video');
      fireEvent.click(addButton);

      // Fill all fields
      fireEvent.change(screen.getByLabelText('Title'), {
        target: { value: 'New Video' },
      });
      fireEvent.change(screen.getByLabelText('Description'), {
        target: { value: 'New description' },
      });
      fireEvent.change(screen.getByLabelText('YouTube URL'), {
        target: { value: 'https://youtube.com/watch?v=xyz789' },
      });
      fireEvent.change(screen.getByLabelText('Transcript'), {
        target: { value: 'Full transcript text here' },
      });

      // Submit
      const addVideoButton = screen.getByRole('button', { name: 'Add Video' });
      fireEvent.click(addVideoButton);

      // Verify ingestVideo was called with correct body
      await waitFor(() => {
        expect(api.ingestVideo).toHaveBeenCalledWith({
          title: 'New Video',
          description: 'New description',
          url: 'https://youtube.com/watch?v=xyz789',
          transcript: 'Full transcript text here',
        });
      });

      // Verify dialog closed
      expect(screen.queryByText('Add New Video')).not.toBeInTheDocument();
    });

    it('shows error message when ingest fails', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Test Video',
          description: 'A test video description',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]);
      vi.spyOn(api, 'ingestVideo').mockRejectedValueOnce(
        new Error('Server error: Invalid URL format'),
      );

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      // Wait for initial load
      await waitFor(() => {
        expect(screen.getByText('Test Video')).toBeInTheDocument();
      });

      // Open the ingest dialog
      const addButton = screen.getByText('+ Add Video');
      fireEvent.click(addButton);

      // Fill all fields
      fireEvent.change(screen.getByLabelText('Title'), {
        target: { value: 'New Video' },
      });
      fireEvent.change(screen.getByLabelText('Description'), {
        target: { value: 'New description' },
      });
      fireEvent.change(screen.getByLabelText('YouTube URL'), {
        target: { value: 'https://youtube.com/watch?v=xyz789' },
      });
      fireEvent.change(screen.getByLabelText('Transcript'), {
        target: { value: 'Full transcript text here' },
      });

      // Submit
      const addVideoButton = screen.getByRole('button', { name: 'Add Video' });
      fireEvent.click(addVideoButton);

      // Should show error
      await waitFor(() => {
        expect(screen.getByText('Server error: Invalid URL format')).toBeInTheDocument();
      });
    });

    it('clears error when dialog is reopened', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Test Video',
          description: 'A test video description',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]);
      vi.spyOn(api, 'ingestVideo').mockRejectedValueOnce(new Error('Some error'));

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      // Wait for initial load
      await waitFor(() => {
        expect(screen.getByText('Test Video')).toBeInTheDocument();
      });

      // Open dialog, trigger error
      const addButton = screen.getByText('+ Add Video');
      fireEvent.click(addButton);

      fireEvent.change(screen.getByLabelText('Title'), {
        target: { value: 'New Video' },
      });
      fireEvent.change(screen.getByLabelText('Description'), {
        target: { value: 'New description' },
      });
      fireEvent.change(screen.getByLabelText('YouTube URL'), {
        target: { value: 'https://youtube.com/watch?v=xyz789' },
      });
      fireEvent.change(screen.getByLabelText('Transcript'), {
        target: { value: 'Transcript' },
      });

      const addVideoButton = screen.getByRole('button', { name: 'Add Video' });
      fireEvent.click(addVideoButton);

      await waitFor(() => {
        expect(screen.getByText('Some error')).toBeInTheDocument();
      });

      // Close dialog
      const cancelButton = screen.getByRole('button', { name: 'Cancel' });
      fireEvent.click(cancelButton);

      // Reopen dialog
      fireEvent.click(addButton);

      // Error should be cleared
      expect(screen.queryByText('Some error')).not.toBeInTheDocument();
    });
  });

  describe('video list rendering', () => {
    it('displays error state with retry button', async () => {
      vi.spyOn(api, 'getVideos').mockRejectedValueOnce(new Error('Network failure'));

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText('Failed to load videos')).toBeInTheDocument();
      });

      expect(screen.getByText('Network failure')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    });

    it('displays empty state when no videos', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([]);

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText('No videos in the knowledge base yet.')).toBeInTheDocument();
      });
    });

    it('displays video cards when videos loaded', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Test Video',
          description: 'A test video description',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]);

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText('Test Video')).toBeInTheDocument();
      });

      expect(screen.getByText('A test video description')).toBeInTheDocument();
      expect(screen.getByText('Watch on YouTube')).toBeInTheDocument();
    });
  });

  describe('channel_title display', () => {
    it('shows channel attribution when channel_title is present', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Test Video',
          description: 'Old description',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
          channel_title: 'Cole Medin',
        },
      ]);

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText('Synced from Cole Medin')).toBeInTheDocument();
      });
      // Description should NOT be shown when channel_title is present
      expect(screen.queryByText(/Old description/)).not.toBeInTheDocument();
    });

    it('falls back to description when channel_title is absent', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Test Video',
          description: 'A test video description',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]);

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText('A test video description')).toBeInTheDocument();
      });
    });

    it('renders nothing when both channel_title and description are absent', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Test Video',
          description: '',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]);

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText('Test Video')).toBeInTheDocument();
      });
      // No attribution text should appear
      expect(screen.queryByText(/Synced from/)).not.toBeInTheDocument();
    });

    it('truncates long description when channel_title is absent', async () => {
      const longDescription = 'A'.repeat(150);
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Test Video',
          description: longDescription,
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]);

      const onClose = vi.fn();
      render(<VideoExplorer isOpen={true} onClose={onClose} />);

      await waitFor(() => {
        expect(screen.getByText('Test Video')).toBeInTheDocument();
      });
      // Should be truncated with ellipsis
      expect(screen.getByText('A'.repeat(117) + '…')).toBeInTheDocument();
    });
  });

  describe('search / filter', () => {
    it('renders search input when videos are present', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'React Hooks Deep Dive',
          description: '',
          url: 'https://yt.be/1',
          created_at: '',
        },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByLabelText('Search videos')).toBeInTheDocument());
    });

    it('does not render search input when library is empty', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() =>
        expect(screen.getByText('No videos in the knowledge base yet.')).toBeInTheDocument(),
      );
      expect(screen.queryByLabelText('Search videos')).not.toBeInTheDocument();
    });

    it('filters video list by title after debounce', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        { id: '1', title: 'React Hooks Deep Dive', description: '', url: '', created_at: '' },
        { id: '2', title: 'TypeScript Tips', description: '', url: '', created_at: '' },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByText('React Hooks Deep Dive')).toBeInTheDocument());

      fireEvent.change(screen.getByLabelText('Search videos'), { target: { value: 'typescript' } });
      // Wait for the debounce (250ms) to fire — React Hooks should be gone
      await waitFor(
        () => expect(screen.queryByText('React Hooks Deep Dive')).not.toBeInTheDocument(),
        { timeout: 1000 },
      );
      // TypeScript Tips is still shown (title text is split by <mark> highlight)
      expect(screen.getByText('TypeScript')).toBeInTheDocument();
    });

    it('restores all videos when search is cleared', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        { id: '1', title: 'React Hooks Deep Dive', description: '', url: '', created_at: '' },
        { id: '2', title: 'TypeScript Tips', description: '', url: '', created_at: '' },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByText('React Hooks Deep Dive')).toBeInTheDocument());

      const input = screen.getByLabelText('Search videos');
      fireEvent.change(input, { target: { value: 'typescript' } });
      await waitFor(
        () => expect(screen.queryByText('React Hooks Deep Dive')).not.toBeInTheDocument(),
        { timeout: 1000 },
      );

      fireEvent.change(input, { target: { value: '' } });
      await waitFor(() => expect(screen.getByText('React Hooks Deep Dive')).toBeInTheDocument(), {
        timeout: 1000,
      });
    });

    it('shows "no match" empty state and echoes the query', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        { id: '1', title: 'React Hooks Deep Dive', description: '', url: '', created_at: '' },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByText('React Hooks Deep Dive')).toBeInTheDocument());

      fireEvent.change(screen.getByLabelText('Search videos'), { target: { value: 'Python' } });
      await waitFor(() => expect(screen.getByText(/No videos match/)).toBeInTheDocument(), {
        timeout: 1000,
      });
      expect(screen.queryByText('React Hooks Deep Dive')).not.toBeInTheDocument();
    });

    it('shows "X of Y videos" filtered count in header when search is active', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        { id: '1', title: 'React Hooks Deep Dive', description: '', url: '', created_at: '' },
        { id: '2', title: 'TypeScript Tips', description: '', url: '', created_at: '' },
        { id: '3', title: 'Vue Basics', description: '', url: '', created_at: '' },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() =>
        expect(screen.getByText('3 videos in knowledge base')).toBeInTheDocument(),
      );

      fireEvent.change(screen.getByLabelText('Search videos'), { target: { value: 'typescript' } });
      await waitFor(() => expect(screen.getByText('1 of 3 videos')).toBeInTheDocument(), {
        timeout: 1000,
      });
    });

    it('matches against channel_title and description in addition to title', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'Episode 4',
          description: '',
          url: '',
          created_at: '',
          channel_title: 'Cole Medin',
        },
        {
          id: '2',
          title: 'Episode 5',
          description: 'Deep dive on retrieval augmented generation',
          url: '',
          created_at: '',
        },
        {
          id: '3',
          title: 'Episode 6',
          description: 'Unrelated content',
          url: '',
          created_at: '',
        },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByText('Episode 4')).toBeInTheDocument());

      // Match by channel_title
      const input = screen.getByLabelText('Search videos');
      fireEvent.change(input, { target: { value: 'cole' } });
      await waitFor(() => expect(screen.queryByText('Episode 5')).not.toBeInTheDocument(), {
        timeout: 1000,
      });
      expect(screen.getByText('Episode 4')).toBeInTheDocument();

      // Match by description
      fireEvent.change(input, { target: { value: 'retrieval' } });
      await waitFor(() => expect(screen.getByText('Episode 5')).toBeInTheDocument(), {
        timeout: 1000,
      });
      expect(screen.queryByText('Episode 4')).not.toBeInTheDocument();
      expect(screen.queryByText('Episode 6')).not.toBeInTheDocument();
    });

    it('resets search state when panel closes and reopens', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValue([
        { id: '1', title: 'React Hooks Deep Dive', description: '', url: '', created_at: '' },
      ]);
      const { rerender } = render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByText('React Hooks Deep Dive')).toBeInTheDocument());

      // Type a search query and wait for debounce
      fireEvent.change(screen.getByLabelText('Search videos'), { target: { value: 'Python' } });
      await waitFor(() => expect(screen.getByText(/No videos match/)).toBeInTheDocument(), {
        timeout: 1000,
      });

      // Close the panel
      rerender(<VideoExplorer isOpen={false} onClose={vi.fn()} />);

      // Reopen the panel
      rerender(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByLabelText('Search videos')).toBeInTheDocument());

      // Search input should be cleared
      expect((screen.getByLabelText('Search videos') as HTMLInputElement).value).toBe('');
      expect(screen.queryByText(/No videos match/)).not.toBeInTheDocument();
    });
  });

  describe('title link', () => {
    it('renders title as a link to video.url for a YouTube video', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '1',
          title: 'YouTube Video Title',
          description: '',
          url: 'https://youtube.com/watch?v=abc123',
          created_at: '2024-01-01T00:00:00Z',
          source_type: 'youtube',
        },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByText('YouTube Video Title')).toBeInTheDocument());

      const link = screen.getByRole('link', { name: 'YouTube Video Title' }) as HTMLAnchorElement;
      expect(link.href).toContain('youtube.com/watch?v=abc123');
      expect(link.target).toBe('_blank');
      expect(link.rel).toContain('noopener');
      expect(link.rel).toContain('noreferrer');
    });

    it('renders title as a link to lesson_url for a Dynamous video', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '2',
          title: 'Dynamous Lesson Title',
          description: '',
          url: 'https://youtube.com/watch?v=xyz',
          created_at: '2024-01-01T00:00:00Z',
          source_type: 'dynamous',
          lesson_url: 'https://community.dynamous.ai/c/lessons/episode-7',
        },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByText('Dynamous Lesson Title')).toBeInTheDocument());

      const link = screen.getByRole('link', {
        name: 'Dynamous Lesson Title',
      }) as HTMLAnchorElement;
      expect(link.href).toContain('community.dynamous.ai');
      expect(link.target).toBe('_blank');
      expect(link.rel).toContain('noopener');
      expect(link.rel).toContain('noreferrer');
    });

    it('renders title as a link to video.url when source_type is dynamous but lesson_url is absent', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '4',
          title: 'Dynamous Video Without Lesson URL',
          description: '',
          url: 'https://youtube.com/watch?v=fallback',
          created_at: '2024-01-01T00:00:00Z',
          source_type: 'dynamous',
          // lesson_url intentionally omitted
        },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() =>
        expect(screen.getByText('Dynamous Video Without Lesson URL')).toBeInTheDocument()
      );

      const link = screen.getByRole('link', {
        name: 'Dynamous Video Without Lesson URL',
      }) as HTMLAnchorElement;
      expect(link.href).toContain('youtube.com/watch?v=fallback');
      expect(link.target).toBe('_blank');
      expect(link.rel).toContain('noopener');
      expect(link.rel).toContain('noreferrer');
    });

    it('renders title as plain text when no URL is available', async () => {
      vi.spyOn(api, 'getVideos').mockResolvedValueOnce([
        {
          id: '3',
          title: 'Untitled Video',
          description: '',
          url: '',
          created_at: '2024-01-01T00:00:00Z',
        },
      ]);
      render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);
      await waitFor(() => expect(screen.getByText('Untitled Video')).toBeInTheDocument());

      // Title should not be a link
      expect(screen.queryByRole('link', { name: 'Untitled Video' })).not.toBeInTheDocument();
    });
  });
});

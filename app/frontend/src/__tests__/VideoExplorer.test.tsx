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
});

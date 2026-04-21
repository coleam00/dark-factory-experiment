import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { VideoExplorer } from '../components/VideoExplorer';
import * as api from '../lib/api';

// Non-admin user: the "+ Add Video" button must not render. Split into its
// own file because vi.mock is hoisted per-module and cannot be changed
// between tests in the same file.
vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: {
      id: 'test-user',
      email: 'user@test',
      is_admin: false,
      messages_used_today: 0,
      messages_remaining_today: 100,
      rate_window_resets_at: null,
    },
    status: 'authed',
    refresh: vi.fn(),
  }),
}));

describe('VideoExplorer admin gate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(api, 'getVideos').mockResolvedValue([]);
  });

  it('hides the "+ Add Video" button for non-admin users', async () => {
    render(<VideoExplorer isOpen={true} onClose={vi.fn()} />);

    // The modal header renders asynchronously after fetchVideos resolves.
    await waitFor(() => {
      expect(screen.getByText('Video Library')).toBeInTheDocument();
    });

    expect(screen.queryByText('+ Add Video')).not.toBeInTheDocument();
  });
});

import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { getSharedConversation } from '../lib/api';
import { SharedConversation } from './SharedConversation';

vi.mock('../lib/api', () => ({
  getSharedConversation: vi.fn(),
}));

describe('SharedConversation', () => {
  it('renders messages and citations, no ChatInput', async () => {
    (getSharedConversation as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      title: 'Shared Chat',
      messages: [
        { id: 'm1', role: 'user', content: 'Hello?' },
        {
          id: 'm2',
          role: 'assistant',
          content: 'Hi!',
          sources: [
            {
              chunk_id: 'c1',
              video_id: 'v1',
              video_title: 'Demo',
              video_url: 'https://www.youtube.com/watch?v=abc',
              start_seconds: 10,
              end_seconds: 20,
              snippet: 'snippet',
              is_cited: true,
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter initialEntries={['/share/tok-123']}>
        <Routes>
          <Route path="/share/:token" element={<SharedConversation />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText('Shared Chat')).toBeInTheDocument());
    expect(screen.getByText('Hello?')).toBeInTheDocument();
    expect(screen.getByText('Hi!')).toBeInTheDocument();
    // Citation chip should appear
    expect(screen.getByRole('button', { name: /Demo/ })).toBeInTheDocument();
    // No chat input in read-only view
    expect(screen.queryByPlaceholderText(/Ask anything/i)).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Type a message/i)).not.toBeInTheDocument();
  });

  it('shows unavailable state on 404', async () => {
    (getSharedConversation as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('Share link not found'),
    );

    render(
      <MemoryRouter initialEntries={['/share/bad-tok']}>
        <Routes>
          <Route path="/share/:token" element={<SharedConversation />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByText('This link is no longer available')).toBeInTheDocument(),
    );
  });
});

import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../lib/api';
import { Sidebar } from './Sidebar';

// Use vi.hoisted to ensure navigateMock is available when vi.mock runs (since vi.mock is hoisted)
const { navigateMock } = vi.hoisted(() => {
  return { navigateMock: vi.fn() };
});

// Mock react-router-dom
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

// Mock the hooks
vi.mock('../hooks/useConversations', () => ({
  useConversations: vi.fn(() => ({
    conversations: [] as api.Conversation[],
    loading: false,
    error: null,
    refetch: vi.fn().mockResolvedValue(undefined),
    rename: vi.fn(),
    filteredConversations: [] as api.Conversation[],
  })),
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: vi.fn(() => ({
    status: 'authed',
    user: {
      id: 'user-1',
      email: 'test@example.com',
      is_admin: false,
      messages_used_today: 5,
      messages_remaining_today: 20,
      rate_window_resets_at: null,
    },
    error: null,
    signup: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  })),
}));

vi.mock('../hooks/useToast', () => ({
  useToast: vi.fn(() => ({
    addToast: vi.fn(),
  })),
}));

describe('Sidebar handleNewChat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigateMock.mockReset();
  });

  describe('guard logic for empty conversations', () => {
    it('should reuse existing empty conversation instead of creating a duplicate', async () => {
      const emptyConversation = {
        id: 'conv-1',
        title: 'New Chat',
        preview: null, // null = no messages sent yet
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      };

      const { useConversations } = await import('../hooks/useConversations');
      vi.mocked(useConversations).mockReturnValueOnce({
        conversations: [emptyConversation] as api.Conversation[],
        loading: false,
        error: null,
        refetch: vi.fn(),
        rename: vi.fn(),
        filteredConversations: [emptyConversation] as api.Conversation[],
      });

      vi.spyOn(api, 'createConversation').mockResolvedValueOnce({} as api.Conversation);

      const onClose = vi.fn();
      const refetch = vi.fn();

      render(
        <MemoryRouter>
          <Sidebar
            activeConversationId="conv-1"
            isOpen={true}
            onClose={onClose}
            conversationsRef={{ current: refetch }}
          />
        </MemoryRouter>,
      );

      // Get all buttons and find the main "New Chat" button
      const buttons = screen.getAllByRole('button');
      const newChatButton = buttons.find(
        (btn) => btn.textContent === 'New Chat' && (btn as HTMLButtonElement).type === 'submit',
      ) as HTMLButtonElement;
      expect(newChatButton).toBeDefined();

      await act(async () => {
        fireEvent.click(newChatButton);
      });

      // createConversation should NOT have been called
      expect(api.createConversation).not.toHaveBeenCalled();
      // onClose should have been called
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('should create new conversation when not on an empty conversation', async () => {
      const nonEmptyConversation = {
        id: 'conv-1',
        title: 'Existing Chat',
        preview: 'Hello world', // has a message preview
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      };

      const newConversation = {
        id: 'conv-2',
        title: 'New Chat',
        preview: null,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      };

      const { useConversations } = await import('../hooks/useConversations');
      vi.mocked(useConversations).mockReturnValueOnce({
        conversations: [nonEmptyConversation] as api.Conversation[],
        loading: false,
        error: null,
        refetch: vi.fn().mockResolvedValue(undefined),
        rename: vi.fn(),
        filteredConversations: [nonEmptyConversation] as api.Conversation[],
      });

      vi.spyOn(api, 'createConversation').mockResolvedValueOnce(
        newConversation as api.Conversation,
      );

      const onClose = vi.fn();
      const refetch = vi.fn();

      render(
        <MemoryRouter>
          <Sidebar
            activeConversationId="conv-1"
            isOpen={true}
            onClose={onClose}
            conversationsRef={{ current: refetch }}
          />
        </MemoryRouter>,
      );

      // Get all buttons and find the main "New Chat" button
      const buttons = screen.getAllByRole('button');
      const newChatButton = buttons.find(
        (btn) => btn.textContent === 'New Chat' && (btn as HTMLButtonElement).type === 'submit',
      ) as HTMLButtonElement;
      expect(newChatButton).toBeDefined();

      await act(async () => {
        fireEvent.click(newChatButton);
        // Wait for async operations to complete
        await new Promise((r) => setTimeout(r, 0));
      });

      // createConversation SHOULD have been called
      expect(api.createConversation).toHaveBeenCalledTimes(1);
      // navigate should have been called
      expect(navigateMock).toHaveBeenCalledWith('/c/conv-2');
      // onClose should have been called
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('should create new conversation when no active conversation', async () => {
      const { useConversations } = await import('../hooks/useConversations');
      vi.mocked(useConversations).mockReturnValueOnce({
        conversations: [] as api.Conversation[],
        loading: false,
        error: null,
        refetch: vi.fn().mockResolvedValue(undefined),
        rename: vi.fn(),
        filteredConversations: [] as api.Conversation[],
      });

      const newConversation = {
        id: 'conv-new',
        title: 'New Chat',
        preview: null,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      };

      vi.spyOn(api, 'createConversation').mockResolvedValueOnce(
        newConversation as api.Conversation,
      );

      const onClose = vi.fn();
      const refetch = vi.fn();

      render(
        <MemoryRouter>
          <Sidebar
            activeConversationId={undefined}
            isOpen={true}
            onClose={onClose}
            conversationsRef={{ current: refetch }}
          />
        </MemoryRouter>,
      );

      // Get all buttons and find the main "New Chat" button
      const buttons = screen.getAllByRole('button');
      const newChatButton = buttons.find(
        (btn) => btn.textContent === 'New Chat' && (btn as HTMLButtonElement).type === 'submit',
      ) as HTMLButtonElement;
      expect(newChatButton).toBeDefined();

      await act(async () => {
        fireEvent.click(newChatButton);
        // Wait for async operations to complete
        await new Promise((r) => setTimeout(r, 0));
      });

      // createConversation SHOULD have been called
      expect(api.createConversation).toHaveBeenCalledTimes(1);
      // navigate should have been called
      expect(navigateMock).toHaveBeenCalledWith('/c/conv-new');
      // onClose should have been called
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('should handle createConversation error and show error message', async () => {
      const { useConversations } = await import('../hooks/useConversations');
      vi.mocked(useConversations).mockReturnValueOnce({
        conversations: [] as api.Conversation[],
        loading: false,
        error: null,
        refetch: vi.fn().mockResolvedValue(undefined),
        rename: vi.fn(),
        filteredConversations: [] as api.Conversation[],
      });

      vi.spyOn(api, 'createConversation').mockRejectedValueOnce(new Error('Network error'));

      const onClose = vi.fn();
      const refetch = vi.fn();

      render(
        <MemoryRouter>
          <Sidebar
            activeConversationId={undefined}
            isOpen={true}
            onClose={onClose}
            conversationsRef={{ current: refetch }}
          />
        </MemoryRouter>,
      );

      // Get all buttons and find the main "New Chat" button
      const buttons = screen.getAllByRole('button');
      const newChatButton = buttons.find(
        (btn) => btn.textContent === 'New Chat' && (btn as HTMLButtonElement).type === 'submit',
      ) as HTMLButtonElement;
      expect(newChatButton).toBeDefined();

      await act(async () => {
        fireEvent.click(newChatButton);
      });

      // Should show error message
      expect(
        await screen.findByText('Could not create conversation. Please try again.'),
      ).toBeInTheDocument();
    });
  });
});

describe('Sidebar filters', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigateMock.mockReset();
  });

  it('shows the filtered empty state and resets filters via Clear filters', async () => {
    vi.spyOn(api, 'getVideos').mockResolvedValue([
      {
        id: 'v1',
        title: 'Video One',
        description: '',
        url: 'https://youtube.com/watch?v=v1',
        created_at: '2024-01-01T00:00:00Z',
      },
    ] as api.Video[]);

    render(
      <MemoryRouter>
        <Sidebar activeConversationId={undefined} isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>,
    );

    // Wait for the video list to populate the dropdown
    await screen.findByRole('option', { name: 'Video One' });
    const select = screen.getByLabelText('Filter by video') as HTMLSelectElement;

    await act(async () => {
      fireEvent.change(select, { target: { value: 'v1' } });
    });

    // filteredConversations is [] (mocked) — filtered empty state shows
    expect(screen.getByText('No conversations match your filters')).toBeInTheDocument();

    const clearButtons = screen.getAllByRole('button', { name: 'Clear filters' });
    await act(async () => {
      fireEvent.click(clearButtons[0]);
    });

    expect(select.value).toBe('');
    expect(screen.queryByText('No conversations match your filters')).not.toBeInTheDocument();
    expect(screen.getByText('No conversations yet')).toBeInTheDocument();
  });

  it('resets date filters via Clear filters', async () => {
    vi.spyOn(api, 'getVideos').mockResolvedValue([] as api.Video[]);

    render(
      <MemoryRouter>
        <Sidebar activeConversationId={undefined} isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>,
    );

    const fromInput = screen.getByLabelText('Filter from date') as HTMLInputElement;
    const toInput = screen.getByLabelText('Filter to date') as HTMLInputElement;

    await act(async () => {
      fireEvent.change(fromInput, { target: { value: '2026-06-01' } });
      fireEvent.change(toInput, { target: { value: '2026-06-07' } });
    });

    expect(fromInput.value).toBe('2026-06-01');
    expect(toInput.value).toBe('2026-06-07');

    const clearButtons = screen.getAllByRole('button', { name: 'Clear filters' });
    await act(async () => {
      fireEvent.click(clearButtons[0]);
    });

    expect(fromInput.value).toBe('');
    expect(toInput.value).toBe('');
  });
});

describe('Sidebar logout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigateMock.mockReset();
  });

  it('shows the user email and a Log out button when authed', async () => {
    render(
      <MemoryRouter>
        <Sidebar activeConversationId={undefined} isOpen={true} onClose={vi.fn()} />
      </MemoryRouter>,
    );

    expect(screen.getByText('test@example.com')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /log out/i })).toBeInTheDocument();
  });

  it('calls logout and navigates to /login when the button is clicked', async () => {
    const logoutMock = vi.fn().mockResolvedValue(undefined);
    const { useAuth } = await import('../hooks/useAuth');
    vi.mocked(useAuth).mockReturnValue({
      status: 'authed',
      user: {
        id: 'user-1',
        email: 'test@example.com',
        is_admin: false,
        messages_used_today: 5,
        messages_remaining_today: 20,
        rate_window_resets_at: null,
      } as never,
      error: null,
      signup: vi.fn(),
      login: vi.fn(),
      logout: logoutMock,
      refresh: vi.fn(),
    });

    const onClose = vi.fn();
    render(
      <MemoryRouter>
        <Sidebar activeConversationId={undefined} isOpen={true} onClose={onClose} />
      </MemoryRouter>,
    );

    const logoutBtn = screen.getByRole('button', { name: /log out/i });
    await act(async () => {
      fireEvent.click(logoutBtn);
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(logoutMock).toHaveBeenCalledTimes(1);
    expect(navigateMock).toHaveBeenCalledWith('/login');
    expect(onClose).toHaveBeenCalled();
  });
});

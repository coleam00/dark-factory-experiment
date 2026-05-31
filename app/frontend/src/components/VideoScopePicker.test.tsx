import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../lib/api';
import { VideoScopePicker } from './VideoScopePicker';

const VIDEOS = [
  { id: 'v1', title: 'First Video', description: '', url: '', created_at: '' },
  { id: 'v2', title: 'Second Video', description: '', url: '', created_at: '' },
];

describe('VideoScopePicker (issue #279)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('selects a video and notifies via onChange', async () => {
    vi.spyOn(api, 'getVideos').mockResolvedValue(VIDEOS);
    const onChange = vi.fn();
    render(<VideoScopePicker selectedIds={[]} onChange={onChange} />);

    // Expand the (initially collapsed) picker.
    fireEvent.click(screen.getByRole('button', { name: /Scope/ }));

    await waitFor(() => expect(screen.getByText('First Video')).toBeInTheDocument());

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);
    expect(onChange).toHaveBeenCalledWith(['v1']);
  });

  it('deselects an already-selected video', async () => {
    vi.spyOn(api, 'getVideos').mockResolvedValue(VIDEOS);
    const onChange = vi.fn();
    render(<VideoScopePicker selectedIds={['v1']} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: /Scope/ }));
    await waitFor(() => expect(screen.getByText('First Video')).toBeInTheDocument());

    const checkboxes = screen.getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]); // v1 is already selected → toggles off
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('summarizes the current selection count', async () => {
    vi.spyOn(api, 'getVideos').mockResolvedValue(VIDEOS);
    render(<VideoScopePicker selectedIds={['v1', 'v2']} onChange={() => {}} />);
    expect(screen.getByText('2 videos')).toBeInTheDocument();
  });
});

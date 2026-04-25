import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../lib/api';
import { useAdminVideos } from './useAdminVideos';

describe('useAdminVideos', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not fetch when enabled=false', async () => {
    const spy = vi.spyOn(api, 'listAdminVideos').mockResolvedValue({ videos: [] });
    renderHook(() => useAdminVideos(undefined, false));

    await new Promise((r) => setTimeout(r, 0));

    expect(spy).not.toHaveBeenCalled();
  });

  it('returns loading=false immediately when enabled=false', () => {
    vi.spyOn(api, 'listAdminVideos').mockResolvedValue({ videos: [] });
    const { result } = renderHook(() => useAdminVideos(undefined, false));

    expect(result.current.loading).toBe(false);
  });

  it('fetches when enabled=true (default)', async () => {
    const spy = vi.spyOn(api, 'listAdminVideos').mockResolvedValue({ videos: [] });
    const { result } = renderHook(() => useAdminVideos());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(spy).toHaveBeenCalledOnce();
    expect(result.current.videos).toEqual([]);
  });

  it('re-fetches when enabled flips from false to true', async () => {
    const spy = vi.spyOn(api, 'listAdminVideos').mockResolvedValue({ videos: [] });
    let enabled = false;
    const { result, rerender } = renderHook(() => useAdminVideos(undefined, enabled));

    await new Promise((r) => setTimeout(r, 0));
    expect(spy).not.toHaveBeenCalled();

    enabled = true;
    rerender();

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(spy).toHaveBeenCalledOnce();
  });
});

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ChatInput } from './ChatInput';

describe('ChatInput', () => {
  describe('Send/Stop button rendering', () => {
    it('shows Send button when not streaming', () => {
      render(<ChatInput onSend={vi.fn()} isStreaming={false} />);
      expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument();
    });

    it('shows Stop button when streaming', () => {
      render(<ChatInput onSend={vi.fn()} isStreaming={true} />);
      expect(screen.getByRole('button', { name: /stop/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /send/i })).not.toBeInTheDocument();
    });

    it('Stop button calls onStop when clicked', () => {
      const onStop = vi.fn();
      render(<ChatInput onSend={vi.fn()} isStreaming={true} onStop={onStop} />);
      fireEvent.click(screen.getByRole('button', { name: /stop/i }));
      expect(onStop).toHaveBeenCalledTimes(1);
    });

    it('Send button calls onSend when clicked', () => {
      const onSend = vi.fn();
      render(<ChatInput onSend={onSend} isStreaming={false} />);
      const input = screen.getByRole('textbox');
      fireEvent.change(input, { target: { value: 'Hello' } });
      fireEvent.click(screen.getByRole('button', { name: /send/i }));
      expect(onSend).toHaveBeenCalledWith('Hello');
    });

    it('input is disabled while streaming', () => {
      render(<ChatInput onSend={vi.fn()} isStreaming={true} />);
      expect(screen.getByRole('textbox')).toBeDisabled();
    });

    it('input is not disabled when not streaming', () => {
      render(<ChatInput onSend={vi.fn()} isStreaming={false} />);
      expect(screen.getByRole('textbox')).not.toBeDisabled();
    });

    it('shows correct placeholder when streaming', () => {
      render(<ChatInput onSend={vi.fn()} isStreaming={true} />);
      expect(screen.getByRole('textbox')).toHaveAttribute(
        'placeholder',
        'Waiting for response…',
      );
    });

    it('shows correct placeholder when not streaming', () => {
      render(<ChatInput onSend={vi.fn()} isStreaming={false} />);
      expect(screen.getByRole('textbox')).toHaveAttribute(
        'placeholder',
        'Ask anything about the video library…',
      );
    });

    it('does not throw when onStop is not provided during streaming', () => {
      render(<ChatInput onSend={vi.fn()} isStreaming={true} />);
      // Stop button exists but has no handler - clicking should not throw
      const stopBtn = screen.getByRole('button', { name: /stop/i });
      expect(() => fireEvent.click(stopBtn)).not.toThrow();
    });
  });
});
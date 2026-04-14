import { useCallback, useRef, useState } from 'react';
import { type Toast, ToastContext } from '../hooks/useToast';

// ── Individual toast notification ────────────────────────────────
function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const isError = toast.type === 'error';
  const isSuccess = toast.type === 'success';

  const bgColor = isError ? '#111827' : isSuccess ? '#111827' : '#111827';
  const borderColor = isError ? '#ef4444' : isSuccess ? '#10b981' : '#3b82f6';
  const iconColor = isError ? '#ef4444' : isSuccess ? '#10b981' : '#3b82f6';

  return (
    <div
      role="alert"
      style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        background: bgColor,
        border: `1px solid ${borderColor}`,
        borderRadius: 10,
        padding: '12px 14px',
        minWidth: 280,
        maxWidth: 380,
        boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
        animation: 'toast-in 0.2s ease',
      }}
    >
      {/* Icon */}
      <div style={{ flexShrink: 0, marginTop: 1, color: iconColor }}>
        {isError ? (
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <circle cx="8" cy="8" r="7" />
            <line x1="8" y1="5" x2="8" y2="8.5" />
            <circle cx="8" cy="11" r="0.5" fill="currentColor" />
          </svg>
        ) : isSuccess ? (
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="8" cy="8" r="7" />
            <polyline points="5,8 7,10 11,6" />
          </svg>
        ) : (
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <circle cx="8" cy="8" r="7" />
            <line x1="8" y1="5" x2="8" y2="8.5" />
            <circle cx="8" cy="11" r="0.5" fill="currentColor" />
          </svg>
        )}
      </div>

      {/* Message */}
      <p
        style={{
          flex: 1,
          margin: 0,
          fontSize: 14,
          color: '#f1f5f9',
          lineHeight: 1.5,
        }}
      >
        {toast.message}
      </p>

      {/* Close button */}
      <button
        onClick={onDismiss}
        aria-label="Dismiss notification"
        style={{
          flexShrink: 0,
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          color: '#94a3b8',
          padding: 2,
          borderRadius: 4,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginTop: -1,
          transition: 'color 0.15s',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.color = '#f1f5f9')}
        onMouseLeave={(e) => (e.currentTarget.style.color = '#94a3b8')}
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 14 14"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        >
          <line x1="3" y1="3" x2="11" y2="11" />
          <line x1="11" y1="3" x2="3" y2="11" />
        </svg>
      </button>
    </div>
  );
}

// ── Toast container + provider ────────────────────────────────────
interface ToastProviderProps {
  children: React.ReactNode;
}

export function ToastProvider({ children }: ToastProviderProps) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const addToast = useCallback((message: string, type: Toast['type'] = 'error') => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setToasts((prev) => [...prev, { id, message, type }]);

    // Auto-dismiss after 4 seconds (within the 3.5–4.5s window)
    const timer = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      timersRef.current.delete(id);
    }, 4000);
    timersRef.current.set(id, timer);
  }, []);

  return (
    <ToastContext.Provider value={{ addToast, removeToast }}>
      {children}

      {/* Toast container — fixed top-right, non-blocking */}
      <div
        aria-live="polite"
        aria-atomic="false"
        style={{
          position: 'fixed',
          top: 16,
          right: 16,
          zIndex: 9999,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          pointerEvents: 'none',
        }}
      >
        {toasts.map((toast) => (
          <div key={toast.id} style={{ pointerEvents: 'auto' }}>
            <ToastItem toast={toast} onDismiss={() => removeToast(toast.id)} />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

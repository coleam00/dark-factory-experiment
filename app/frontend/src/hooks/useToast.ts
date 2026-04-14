import { createContext, useContext } from 'react';

export interface Toast {
  id: string;
  message: string;
  type: 'error' | 'info' | 'success';
}

export interface ToastContextValue {
  addToast: (message: string, type?: Toast['type']) => void;
  removeToast: (id: string) => void;
}

export const ToastContext = createContext<ToastContextValue>({
  addToast: () => {},
  removeToast: () => {},
});

export function useToast() {
  return useContext(ToastContext);
}

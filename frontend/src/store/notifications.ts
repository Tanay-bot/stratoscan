import { create } from 'zustand'

export type ToastKind = 'success' | 'error' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  title: string
  message?: string
}

interface ToastState {
  toasts: Toast[]
  push: (kind: ToastKind, title: string, message?: string) => number
  dismiss: (id: number) => void
}

let nextId = 1

export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (kind, title, message) => {
    const id = nextId++
    set((s) => ({ toasts: [...s.toasts, { id, kind, title, message }] }))
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, 4200)
    return id
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}))

export const toast = {
  success: (title: string, message?: string) =>
    useToasts.getState().push('success', title, message),
  error: (title: string, message?: string) => useToasts.getState().push('error', title, message),
  info: (title: string, message?: string) => useToasts.getState().push('info', title, message),
}

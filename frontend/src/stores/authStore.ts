import { create } from 'zustand';

interface User {
  username: string;
  role: string;
}

interface AuthState {
  token: string | null;
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => void;
  initialize: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem('x9_token'),
  user: null,
  isAuthenticated: !!localStorage.getItem('x9_token'),
  isLoading: false,
  error: null,

  login: async (username: string, password: string) => {
    set({ isLoading: true, error: null });
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Usuário ou senha incorretos.');
      }

      const data = await res.json();
      localStorage.setItem('x9_token', data.access_token);
      set({
        token: data.access_token,
        user: data.user,
        isAuthenticated: true,
        isLoading: false,
        error: null,
      });
      return true;
    } catch (err: any) {
      set({ error: err.message, isLoading: false });
      return false;
    }
  },

  logout: () => {
    localStorage.removeItem('x9_token');
    set({ token: null, user: null, isAuthenticated: false });
  },

  initialize: async () => {
    const token = localStorage.getItem('x9_token');
    if (!token) {
      set({ isAuthenticated: false, user: null });
      return;
    }

    try {
      const res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        const user = await res.json();
        set({ user, isAuthenticated: true });
      } else {
        localStorage.removeItem('x9_token');
        set({ token: null, user: null, isAuthenticated: false });
      }
    } catch {
      // offline ou erro no backend
    }
  },
}));

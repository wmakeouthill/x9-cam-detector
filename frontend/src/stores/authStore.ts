import { create } from 'zustand';

interface User {
  username: string;
  role: string;
}

interface AuthState {
  token: string | null;
  user: User | null;
  isAuthenticated: boolean;
  isInitialized: boolean;
  isLoading: boolean;
  error: string | null;
  login: (username: string, password: string) => Promise<boolean>;
  logout: (reason?: unknown) => void;
  initialize: () => Promise<void>;
}

const initialToken = localStorage.getItem('x9_token');

export const useAuthStore = create<AuthState>((set) => ({
  token: initialToken,
  user: null,
  isAuthenticated: false,
  isInitialized: !initialToken,
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
        isInitialized: true,
        isLoading: false,
        error: null,
      });
      return true;
    } catch (err: any) {
      set({ error: err.message, isLoading: false });
      return false;
    }
  },

  logout: (reason?: unknown) => {
    localStorage.removeItem('x9_token');
    set({
      token: null,
      user: null,
      isAuthenticated: false,
      isInitialized: true,
      error: typeof reason === 'string' ? reason : null,
    });
  },

  initialize: async () => {
    const token = localStorage.getItem('x9_token');
    if (!token) {
      set({ token: null, isAuthenticated: false, isInitialized: true, user: null });
      return;
    }

    try {
      const res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${token}` },
      });

      if (res.ok) {
        const user = await res.json();
        set({ token, user, isAuthenticated: true, isInitialized: true, error: null });
      } else {
        localStorage.removeItem('x9_token');
        set({
          token: null,
          user: null,
          isAuthenticated: false,
          isInitialized: true,
          error: res.status === 401 ? 'Sessão expirada. Faça login novamente.' : null,
        });
      }
    } catch {
      // Backend momentaneamente offline: não monta streams sem validação, mas libera UI
      set({ isInitialized: true });
    }
  },
}));

// Interceptador único de 401 para todas as chamadas /api/ (exceto login):
// evita abas abertas >TTL ficarem presas em loop de 401 após expiração do JWT.
let authInterceptorInstalled = false;
export function installAuthFetchInterceptor() {
  if (authInterceptorInstalled || typeof window === 'undefined') return;
  authInterceptorInstalled = true;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const res = await nativeFetch(input, init);
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    if (res.status === 401 && url.includes('/api/') && !url.includes('/api/auth/login')) {
      const state = useAuthStore.getState();
      if (state.token || state.isAuthenticated) {
        state.logout('Sessão expirada. Faça login novamente.');
      }
    }
    return res;
  };
}

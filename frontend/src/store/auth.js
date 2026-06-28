import { create } from 'zustand';
import {
  api,
  setAccessToken,
  setRefreshHandler,
  setAuthFailureHandler,
} from '../lib/api';

// ClauseOps auth store.
//
// Source of truth for the in-memory access token and the current user. The access token is
// mirrored into the api client (src/lib/api.js) so non-React modules attach it as a Bearer
// header. The refresh token lives in an httpOnly cookie managed by the backend and is never
// read here.
//
// Session lifecycle:
//   - login/register   -> obtain an access token (+ refresh cookie) and load the user.
//   - initialize()      -> on app start, attempt a silent refresh using the cookie, then
//                          loadMe to restore a session. If refresh fails, stay logged out.
//   - refresh()         -> rotate the access token; deduped so concurrent 401s share one call.
//   - logout()          -> invalidate the refresh token server-side and clear local state.
//
// State flags:
//   - isAuthenticated   -> a valid access token is held.
//   - loading           -> an auth request (login/register/initialize) is in flight.
//   - initialized       -> the initial silent-refresh attempt has settled; routing waits on
//                          this before deciding whether to redirect unauthenticated users.

// Module-level dedup for concurrent refreshes (e.g. several protected calls 401 at once).
let refreshPromise = null;

export const useAuthStore = create((set, get) => ({
  user: null,
  accessToken: null,
  isAuthenticated: false,
  loading: false,
  initialized: false,

  // Apply (or clear) the access token everywhere it matters.
  applyToken: (token) => {
    setAccessToken(token || null);
    set({ accessToken: token || null, isAuthenticated: Boolean(token) });
  },

  // Authenticate with email + password, then load the current user.
  login: async (email, password) => {
    set({ loading: true });
    try {
      const data = await api.post('/auth/login', { email, password });
      get().applyToken(data?.access_token ?? null);
      const user = await api.get('/me');
      set({ user, loading: false });
      return user;
    } catch (err) {
      set({ loading: false });
      throw err;
    }
  },

  // Create an account, then sign in with the same credentials.
  register: async (email, password) => {
    set({ loading: true });
    try {
      await api.post('/auth/register', { email, password });
    } catch (err) {
      set({ loading: false });
      throw err;
    }
    // login manages its own loading flag from here.
    return get().login(email, password);
  },

  // Invalidate the refresh token server-side and clear local session state. Local state is
  // cleared regardless of whether the network call succeeds, so logout always "works".
  logout: async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // Best-effort: clear locally even if the server call fails.
    }
    get().clearSession();
  },

  // Rotate the access token using the httpOnly refresh cookie. Deduped: simultaneous callers
  // share a single in-flight request. Resolves to the new token, or null on failure.
  refresh: async () => {
    if (refreshPromise) return refreshPromise;
    refreshPromise = (async () => {
      try {
        const data = await api.post('/auth/refresh');
        const token = data?.access_token ?? null;
        get().applyToken(token);
        return token;
      } finally {
        refreshPromise = null;
      }
    })();
    return refreshPromise;
  },

  // Load the current user into state.
  loadMe: async () => {
    const user = await api.get('/me');
    set({ user });
    return user;
  },

  // On app start, try to restore a session via a silent refresh + loadMe. Always settles
  // `initialized` so protected routes can stop waiting and decide.
  initialize: async () => {
    if (get().initialized || get().loading) return;
    set({ loading: true });
    try {
      const token = await get().refresh();
      if (token) {
        await get().loadMe();
      }
    } catch {
      get().clearSession();
    } finally {
      set({ loading: false, initialized: true });
    }
  },

  // Clear all session state.
  clearSession: () => {
    setAccessToken(null);
    set({ user: null, accessToken: null, isAuthenticated: false });
  },
}));

// Wire the api client's refresh-on-401 recovery to this store. A 401 on a protected call
// triggers a single refresh + retry; a failed refresh clears the session.
setRefreshHandler(() => useAuthStore.getState().refresh());
setAuthFailureHandler(() => useAuthStore.getState().clearSession());

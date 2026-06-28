// ClauseOps API client.
//
// Convention (see frontend/README.md and vite.config.js):
//   - All HTTP calls go through the `/api` prefix. In dev, Vite proxies `/api/*` to the
//     FastAPI backend and strips the prefix, so `request('/auth/login')` hits the backend
//     route `/auth/login`. In production a reverse proxy applies the same mapping.
//   - WebSocket connections use the `/ws` prefix, forwarded to the backend unchanged.
//   - The JWT access token is held in memory and attached as a Bearer header on every
//     request. The refresh token lives in an httpOnly cookie managed by the backend, so it
//     is never read here. Full auth wiring (login/refresh/logout) lands in task 16.

export const API_BASE = '/api';
export const WS_BASE = '/ws';

// In-memory access token. The auth store (src/store/auth.js) is the source of truth and
// keeps this in sync via setAccessToken so non-React modules can read it.
let accessToken = null;

export function setAccessToken(token) {
  accessToken = token || null;
}

export function getAccessToken() {
  return accessToken;
}

// Refresh-on-401 wiring. The auth store (src/store/auth.js) registers a refresh handler and
// an auth-failure handler so the API client can transparently recover from an expired access
// token: a 401 on a protected call triggers a single silent refresh + retry, and a failed
// refresh logs the user out. These live here (rather than importing the store) to avoid a
// circular dependency and to keep the client usable from non-React modules.
let refreshHandler = null;
let authFailureHandler = null;

/**
 * Register the function used to attempt a token refresh. It must resolve to the new access
 * token (truthy) on success, or a falsy value / rejection on failure.
 * @param {(() => Promise<string|null>)|null} fn
 */
export function setRefreshHandler(fn) {
  refreshHandler = fn || null;
}

/**
 * Register the function invoked when a refresh attempt fails, so the session can be cleared.
 * @param {(() => void)|null} fn
 */
export function setAuthFailureHandler(fn) {
  authFailureHandler = fn || null;
}

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

/**
 * Perform a JSON HTTP request against the backend.
 *
 * @param {string} path  Backend path WITHOUT the `/api` prefix, e.g. `/auth/login`.
 * @param {RequestInit & { json?: unknown }} [options]
 * @returns {Promise<any>} Parsed JSON body, or null for 204 responses.
 */
export async function request(path, options = {}) {
  // `_retry` guards against infinite refresh loops: it is set only on the single retry that
  // follows a successful silent refresh, and is never forwarded to fetch.
  const { json, headers, _retry, ...rest } = options;

  const finalHeaders = new Headers(headers || {});
  if (accessToken) {
    finalHeaders.set('Authorization', `Bearer ${accessToken}`);
  }

  let body = rest.body;
  if (json !== undefined) {
    finalHeaders.set('Content-Type', 'application/json');
    body = JSON.stringify(json);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    // Include cookies so the httpOnly refresh-token cookie is sent on refresh/logout.
    credentials: 'include',
    ...rest,
    headers: finalHeaders,
    body,
  });

  // Transparent refresh-on-401. Skip the auth endpoints themselves (a 401 from
  // /auth/refresh or /auth/login is terminal) and skip the post-refresh retry to avoid loops.
  if (
    response.status === 401 &&
    !_retry &&
    refreshHandler &&
    !path.startsWith('/auth/')
  ) {
    let newToken = null;
    try {
      newToken = await refreshHandler();
    } catch {
      newToken = null;
    }
    if (newToken) {
      return request(path, { ...options, _retry: true });
    }
    if (authFailureHandler) {
      authFailureHandler();
    }
  }

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get('content-type') || '';
  const data = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message =
      (data && typeof data === 'object' && (data.detail || data.message)) ||
      `Request failed with status ${response.status}`;
    throw new ApiError(message, response.status, data);
  }

  return data;
}

export const api = {
  get: (path, options) => request(path, { ...options, method: 'GET' }),
  post: (path, json, options) => request(path, { ...options, method: 'POST', json }),
  patch: (path, json, options) => request(path, { ...options, method: 'PATCH', json }),
  put: (path, json, options) => request(path, { ...options, method: 'PUT', json }),
  delete: (path, options) => request(path, { ...options, method: 'DELETE' }),
};

/**
 * Build the WebSocket URL for a contract's live-progress channel.
 * The backend authenticates the socket via a `token` query param (see design §Component 5),
 * since browsers cannot set Authorization headers on WebSocket handshakes.
 *
 * @param {string|number} contractId
 * @param {string} [token]  Access token; defaults to the in-memory token.
 * @returns {string} A ws:// or wss:// URL.
 */
export function contractProgressSocketUrl(contractId, token = accessToken) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const base = `${proto}://${window.location.host}${WS_BASE}/contracts/${contractId}`;
  return token ? `${base}?token=${encodeURIComponent(token)}` : base;
}

/**
 * Open a WebSocket to a contract's progress channel. Returns the raw WebSocket so callers
 * (task 17) can attach onmessage/onerror handlers. Falls back to status polling is the
 * caller's responsibility.
 *
 * @param {string|number} contractId
 * @returns {WebSocket}
 */
export function openContractProgressSocket(contractId) {
  return new WebSocket(contractProgressSocketUrl(contractId));
}

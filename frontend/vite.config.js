import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// ClauseOps frontend Vite config.
//
// API / proxy convention (documented in README.md):
//   - The FastAPI backend serves routes at the ROOT (e.g. `/auth/login`, `/contracts`,
//     `/tasks`, `/dashboard/summary`, `/calendar`, `/notifications`) and a WebSocket at
//     `/ws/contracts/{id}`.
//   - The SPA's API client prepends a `/api` prefix to every HTTP call. In development the
//     Vite dev server proxies `/api/*` to the backend and STRIPS the `/api` prefix, so
//     `GET /api/auth/login` reaches the backend as `GET /auth/login`.
//   - WebSocket calls use the `/ws` prefix as-is. The backend already serves `/ws/...`, so
//     `/ws` is proxied WITHOUT a rewrite (ws: true for upgrade support).
//   - In production, a reverse proxy (e.g. Nginx) is expected to apply the same mapping:
//     `/api` -> backend root, `/ws` -> backend `/ws`.
const BACKEND_HTTP = process.env.VITE_BACKEND_ORIGIN || 'http://localhost:8000';
const BACKEND_WS = process.env.VITE_BACKEND_WS_ORIGIN || 'ws://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // HTTP API: strip the `/api` prefix before forwarding to the backend root.
      '/api': {
        target: BACKEND_HTTP,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      // WebSocket: forward `/ws/...` unchanged (backend serves `/ws/contracts/{id}`).
      '/ws': {
        target: BACKEND_WS,
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});

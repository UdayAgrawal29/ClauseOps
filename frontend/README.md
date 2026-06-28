# ClauseOps Frontend

React 18 + Vite single-page app (JavaScript/JSX — **not** TypeScript) for the ClauseOps
web platform. This directory is the SPA only; the FastAPI backend lives in the `app/`
Python package at the repository root.

## Stack

- React 18 + Vite (`@vitejs/plugin-react`)
- Tailwind CSS (+ PostCSS + Autoprefixer)
- React Router v6 (`react-router-dom`)
- TanStack Query (`@tanstack/react-query`) for server state / polling
- Zustand for client state (auth, filters)
- react-hook-form + Zod (+ `@hookform/resolvers`) for forms and validation

## Install and run

```bash
cd frontend
npm install
npm run dev      # starts the Vite dev server on http://localhost:5173
```

Other scripts:

```bash
npm run build    # production build into dist/
npm run preview  # preview the production build
```

The dev server expects the FastAPI backend to be running on `http://localhost:8000`
(see the proxy convention below).

## API / proxy / base-URL convention

The backend serves its routes at the **root** (e.g. `/auth/login`, `/contracts`,
`/tasks`, `/dashboard/summary`, `/calendar`, `/notifications`) and a WebSocket at
`/ws/contracts/{id}?token=...`.

The SPA uses a stable, prefix-based convention so the same client code works in dev and
behind a production reverse proxy:

| Concern | Client prefix | Dev proxy target | Mapping |
|---|---|---|---|
| HTTP API | `/api` | `http://localhost:8000` | `/api` is **stripped** → backend root. `GET /api/auth/login` → `GET /auth/login` |
| WebSocket | `/ws` | `ws://localhost:8000` | forwarded **unchanged** (backend already serves `/ws/...`) |

- The API client (`src/lib/api.js`) prepends `/api` to every HTTP call. Call it with the
  backend path only — e.g. `api.post('/auth/login', body)`.
- WebSocket URLs are built with `contractProgressSocketUrl(contractId)` /
  `openContractProgressSocket(contractId)`, which target `/ws/contracts/{id}` and attach
  the access token as a `?token=` query param (browsers cannot set Authorization headers on
  WebSocket handshakes).
- The proxy is configured in `vite.config.js`. Override the backend targets with the
  `VITE_BACKEND_ORIGIN` / `VITE_BACKEND_WS_ORIGIN` env vars (see `.env.example`).
- In production, configure your reverse proxy (e.g. Nginx) with the same mapping:
  `/api` → backend root, `/ws` → backend `/ws`.

## Authentication

- The **access token** is held in memory in the Zustand auth store (`src/store/auth.js`)
  and mirrored into the API client so it is attached as a `Bearer` header on every request.
- The **refresh token** is stored in an httpOnly cookie managed by the backend; the client
  never reads it. Requests are sent with `credentials: 'include'` so the cookie flows on
  refresh/logout.
- The store in this scaffold is a stub. Full auth wiring (forms, refresh-on-401, logout,
  protected routes) is implemented in task 16.

## Structure

```
frontend/
├─ index.html
├─ vite.config.js          # dev-server proxy (/api + /ws)
├─ tailwind.config.js
├─ postcss.config.js
├─ src/
│  ├─ main.jsx             # mounts React, wraps QueryClientProvider + BrowserRouter
│  ├─ App.jsx              # placeholder route (feature screens land in tasks 16-20)
│  ├─ index.css            # Tailwind directives
│  ├─ lib/
│  │  ├─ api.js            # HTTP + WebSocket client, JWT attachment
│  │  └─ queryClient.js    # shared TanStack Query client
│  └─ store/
│     └─ auth.js           # Zustand auth store stub
└─ public/
   └─ vite.svg
```

# Running ClauseOps Locally

ClauseOps is offline-first and runs on a local Python virtualenv, a local PostgreSQL, and a local Redis. There is **no Docker** in this project.

## Prerequisites

You need four things available on your machine:

- **Python 3.12** (a `venv/` is already in the repo)
- **Node.js 20+** (for the frontend)
- **PostgreSQL** running on `localhost:5432`
- **Redis** running on `localhost:6379`

### Installing PostgreSQL and Redis on Windows

- **PostgreSQL** — install the official Windows installer from https://www.postgresql.org/download/windows/. After install, create the database/role that matches the default connection string:
  ```sql
  -- run in psql as a superuser
  CREATE ROLE clauseops WITH LOGIN PASSWORD 'clauseops';
  CREATE DATABASE clauseops OWNER clauseops;
  ```
- **Redis** — Redis has **no official native Windows build**, so run the genuine Redis binary inside **WSL** (recommended):
  ```bat
  wsl --install            :: one-time (installs Ubuntu); reboot if prompted
  ```
  then in the Ubuntu shell:
  ```bash
  sudo apt update && sudo apt install redis-server
  sudo service redis-server start
  redis-cli ping           # -> PONG
  ```
  WSL2 forwards localhost, so the Windows-side app connects at `localhost:6379` with no config change. (If you'd rather not use WSL, **Memurai** is a Redis-compatible Windows service that also listens on `localhost:6379`.)

> If you point the app at different hosts/credentials, set the `CLAUSEOPS_DATABASE_URL` and `CLAUSEOPS_REDIS_URL` environment variables (see `app/config.py`).

## 1. Install dependencies

```bat
venv\Scripts\python.exe -m pip install -r requirements-backend.txt
venv\Scripts\python.exe -m pip install -r requirements.txt
```

> `requirements.txt` pulls the heavy ML stack (Docling, spaCy, transformers, torch) used only by the ML worker. Skip it if you only want the API + frontend.

## 2. Apply database migrations

```bat
venv\Scripts\alembic.exe upgrade head
```

## 3. Start the services

> **Windows note:** Celery's default `prefork` pool fails on Windows with `PermissionError: [WinError 5]`. Always add `--pool=solo` to worker commands on Windows.

**Terminal 1 — API**
```bat
venv\Scripts\uvicorn.exe app.web.main:app --reload
```

**Terminal 2 — Celery worker (both queues)**

In **PowerShell** (your prompt shows `PS C:\...>`):
```powershell
$env:CLAUSEOPS_ML_WORKER='1'
venv\Scripts\celery.exe -A app.processing.celery_app worker -Q ml,default --pool=solo --loglevel=info
```
In **Command Prompt (cmd)** instead:
```bat
set CLAUSEOPS_ML_WORKER=1
venv\Scripts\celery.exe -A app.processing.celery_app worker -Q ml,default --pool=solo --loglevel=info
```
- `-Q ml,default` → this worker handles both heavy contract processing and light reminder tasks.
- `--pool=solo` → required on Windows (the default `prefork` pool fails with `PermissionError: [WinError 5]`).

> **Don't mix shells:** in PowerShell, `set X=1` does NOT set an env var and `&&` is not a valid separator — use the `$env:` form above. The embedded-beat flag `-B` is also **not supported on Windows**; run Beat separately (Terminal 4) if you need it.

**Terminal 3 — frontend**
```bat
cd frontend
npm install      :: first time only
npm run dev
```

Open the URL Vite prints (typically `http://localhost:5173`). The Vite dev server proxies `/api` and `/ws` to the API on `http://localhost:8000`.

**Terminal 4 — Celery Beat (OPTIONAL — only needed for periodic reminders)**
```bat
venv\Scripts\celery.exe -A app.processing.celery_app beat --loglevel=info
```

> **Tip:** For normal development you can skip Beat (Terminal 4) — it only fires the periodic reminder scan. You can also skip the worker entirely (Terminal 2) if you just want to click around the API/UI without real PDF processing; uploads will stay in `PENDING` but auth, dashboard, etc. all work.

## Running the tests

```bat
:: Full backend suite (unit + integration + property-based)
venv\Scripts\python.exe -m pytest app/ tests/storage -q

:: Frontend build (compile check)
cd frontend && npm run build
```

## Continuous Integration (optional)

`.github/workflows/ci.yml` runs the backend test suite (async-SQLite based, so no PostgreSQL/Redis needed) and the frontend build on push/PR. It does not use Docker.

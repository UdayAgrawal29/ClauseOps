---
marp: true
---

# ClauseOps — Complete Project Guide

> A full knowledge-transfer document for the ClauseOps web platform. Read this top to bottom and you will understand the product, the architecture, every module, the data flow, and how to run and maintain the system — with no prior knowledge assumed.

---

## Table of Contents

1. [What ClauseOps Is](#1-what-clauseops-is)
2. [The Two Guiding Principles](#2-the-two-guiding-principles)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Technology Stack & Why](#4-technology-stack--why)
5. [Repository / Folder Structure](#5-repository--folder-structure)
6. [The Database (Data Layer)](#6-the-database-data-layer)
7. [Authentication & Security](#7-authentication--security)
8. [Multi-Tenant Ownership Scoping](#8-multi-tenant-ownership-scoping)
9. [Storage Abstraction](#9-storage-abstraction)
10. [The Asynchronous ML Processing Pipeline](#10-the-asynchronous-ml-processing-pipeline)
11. [Live Progress (WebSocket + Redis)](#11-live-progress-websocket--redis)
12. [The Web API (All Endpoints)](#12-the-web-api-all-endpoints)
13. [Reminders & Notifications](#13-reminders--notifications)
14. [The Frontend (React SPA)](#14-the-frontend-react-spa)
15. [The Signature Feature: Grounded Source-Span Viewer](#15-the-signature-feature-grounded-source-span-viewer)
16. [Correctness Properties & Testing Strategy](#16-correctness-properties--testing-strategy)
17. [End-to-End Walkthrough](#17-end-to-end-walkthrough)
18. [Major Design Decisions (and the reasoning)](#18-major-design-decisions-and-the-reasoning)
19. [How to Run the Project](#19-how-to-run-the-project)
20. [Glossary](#20-glossary)

---

## 1. What ClauseOps Is

ClauseOps is an **offline-first web platform that turns a static contract PDF into a living, deadline-first task tracker** — built for people who do *not* have a legal team.

The user journey in one sentence: **upload a contract PDF → watch it being analyzed in real time → work a prioritized list of deadline-bearing obligations**, where every obligation shows the exact words in the contract it was extracted from.

Concretely, a user can:
- Register / log in with email + password.
- Upload a contract PDF.
- Watch a live progress bar as the document is segmented, classified, and mined for obligations.
- Browse the extracted **tasks** (obligations) with their **source clause**, the **highlighted span** the task was extracted from, a **confidence score**, and a **due date**.
- See a **deadline-first dashboard**, a filterable **task list**, a **calendar**, and a **review queue** for uncertain items.
- Change a task's status, correct extracted fields (with a full audit trail), and receive **in-app reminders** for upcoming deadlines.

The heavy lifting — actually reading the PDF and extracting obligations — is done by a **pre-existing machine-learning package called `clauseops`** that this project *wraps but never rewrites*. Everything else (the web app, the database, the queueing, the UI) is what this project added.

---

## 2. The Two Guiding Principles

Every design decision traces back to two product non-negotiables:

1. **Deadline-first.** The home screen answers one question: *"What's due and what needs my attention?"* Tasks are ordered by deadline; the dashboard surfaces upcoming deadlines and a review count.

2. **Trust-but-verify.** The system never asks you to blindly trust an AI extraction. Every task shows:
   - the **source clause text** it came from,
   - the **exact highlighted characters** that produced the obligation (the "grounded span"),
   - a **confidence score**, and
   - a **"needs review" flag** for anything the pipeline was unsure about.

The trust-but-verify experience is made possible by a guarantee from the ML pipeline called the **grounding invariant**: every extracted party and action is a *verbatim substring* of the source clause. Because of that, the platform can store character offsets and later highlight the exact source characters. This is the project's signature feature (see [§15](#15-the-signature-feature-grounded-source-span-viewer)).

A third operational principle shapes the whole stack: **offline-first**. The entire system runs on a single machine with **no external accounts**: local PostgreSQL, local Redis, local-filesystem file storage, the project's own JWT auth (no OAuth), and **in-app notifications only** (no real email in the MVP). There is no Firebase anywhere, and no Docker. The ML worker makes **no outbound network calls**, so contract data never leaves the host.

---

## 3. High-Level Architecture

The system has **four layers**:

```
┌──────────────────────────────────────────────────────────────────────┐
│  React SPA (frontend/)                                                 │
│  Dashboard · Upload · Grounded Viewer · Tasks · Calendar · Review      │
└───────────────┬──────────────────────────────────┬───────────────────┘
                │ HTTPS (/api/*)                     │ WebSocket (/ws/*)
                ▼                                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│  FastAPI Web Layer (app/web/)                                          │
│  auth · contracts CRUD · upload · status · read APIs · WebSocket       │
└───────┬───────────────────┬───────────────────────┬───────────────────┘
        │                   │                        │
        ▼                   ▼                        ▼
┌───────────────┐   ┌───────────────┐      ┌──────────────────────────┐
│ PostgreSQL    │   │ Redis         │      │ Object storage           │
│ (app/data/)   │   │ broker/pubsub │      │ local FS (app/storage/)  │
└───────────────┘   └───────┬───────┘      └──────────────────────────┘
                            │ enqueue / pub-sub
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Celery Processing Layer (app/processing/)                             │
│  ml queue (heavy: runs clauseops, warm models)                         │
│  default queue (light: reminder scan via Celery Beat)                  │
└───────────────┬────────────────────────────────────────────────────────┘
                │ imports, never rewrites
                ▼
        ┌───────────────────────┐
        │ clauseops package     │  ← the existing ML pipeline
        └───────────────────────┘
```

**Why this shape?**
- The web layer must stay responsive, but analyzing a contract takes ~40 seconds and needs ~1.5–2 GB of ML models in memory. So the heavy work is pushed onto a **background worker** (Celery) on a dedicated **`ml` queue**, isolated from light tasks.
- The browser needs to see progress *as it happens*, so the worker publishes progress to **Redis pub/sub** and the API relays it to the browser over a **WebSocket** (with a polling fallback).
- All file access, database access, and (future) email go behind **thin interfaces** so the offline MVP can later swap in managed services via configuration without rewriting code.

---

## 4. Technology Stack & Why

### Backend (Python)

| Concern | Choice | Why |
|---|---|---|
| API framework | **FastAPI + Uvicorn** | Native WebSocket support (for live progress), built-in OpenAPI docs, Pydantic validation, async endpoints. Already present in the repo. |
| ORM + migrations | **SQLAlchemy 2.0 (async) + Alembic** | Modern typed ORM with async support; Alembic for versioned schema migrations. |
| Validation | **Pydantic v2** | Request/response schemas and settings. |
| Database | **PostgreSQL** | Relational integrity, JSONB columns, FK cascades. |
| Broker / cache / pub-sub | **Redis** | One service does Celery broker + result backend + progress pub/sub. |
| Task queue | **Celery + Celery Beat** | Background processing (`ml` queue) and periodic jobs (reminder scan). |
| Object storage | **Local filesystem** behind a swappable interface | Offline MVP; can later become R2/Supabase via config. |
| Auth | **Own JWT (email + password)**, Argon2 via passlib | No OAuth/Firebase in MVP; provider abstracted for later. |
| ML | Existing **`clauseops`** package (Docling, spaCy, transformers, torch) | Wrapped by the heavy worker; never rewritten. |
| Tests | **pytest + Hypothesis** | Unit, integration, and property-based tests. |

### Frontend (JavaScript — *not* TypeScript)

| Concern | Choice |
|---|---|
| Framework / build | React 18 + Vite (JS/JSX) |
| Styling | Tailwind CSS |
| Server state | TanStack Query (caching + polling) |
| Client state | Zustand (auth + filters) |
| Routing | React Router v6 |
| Forms + validation | react-hook-form + Zod |
| Realtime | Native WebSocket (fallback: TanStack Query polling) |

> **Note:** The frontend is deliberately JavaScript/JSX, not TypeScript. This was a locked decision.

---

## 5. Repository / Folder Structure

```
ClauseOps/
├── clauseops/                  # EXISTING ML pipeline package (imported, never rewritten)
│   ├── segmentation/           #   segment_contract()
│   ├── clause_classification/  #   classify_clauses()
│   ├── entity_extraction/      #   extract_entities_from_contract()
│   └── obligation_detection/   #   deontic_classifier, date_normalizer, task_generator
│
├── app/                        # THE BACKEND (everything this project added)
│   ├── config.py               # Environment-driven settings (CLAUSEOPS_* env vars)
│   ├── data/                   # Data layer
│   │   ├── database.py         #   async engine, session factory, declarative Base
│   │   ├── enums.py            #   ContractStatus, TaskStatus, ReminderChannel
│   │   ├── models.py           #   the 7 ORM tables + validation rules
│   │   └── ownership.py        #   user-scoped queries + ownership guards
│   ├── auth/                   # Auth core (framework-agnostic)
│   │   └── security.py         #   Argon2 hashing + JWT issue/verify/refresh/rotate
│   ├── storage/                # Swappable object storage
│   │   ├── base.py             #   StorageBackend interface (put/get/delete/url)
│   │   └── local.py            #   LocalFilesystemStorage implementation
│   ├── processing/             # Celery processing layer
│   │   ├── celery_app.py       #   Celery app, queues, routing, warm-load scaffolding
│   │   ├── enqueue.py          #   abstraction the web layer calls to enqueue work
│   │   ├── ml.py               #   the pipeline task chain (wraps clauseops) + persistence
│   │   ├── spans.py            #   pure grounded-span offset computation
│   │   ├── progress.py         #   per-stage progress publishing (Redis + DB)
│   │   └── reminders.py        #   Celery Beat reminder scan (single delivery)
│   └── web/                    # FastAPI web layer
│       ├── main.py             #   create_app() factory; mounts all routers
│       ├── dependencies.py     #   current-user dependency, DbSession alias
│       ├── schemas.py          #   Pydantic request/response models
│       ├── revocation.py       #   refresh-token revocation store
│       ├── auth_router.py      #   /auth/* + /me
│       ├── contracts_router.py #   POST/GET/DELETE /contracts, GET /contracts/{id}
│       ├── progress_router.py  #   WS /ws/contracts/{id} + GET /contracts/{id}/status
│       ├── tasks_router.py     #   GET /tasks (filters) + PATCH /tasks/{id}
│       ├── review_router.py    #   GET /review-queue
│       ├── dashboard_router.py #   GET /dashboard/summary + GET /calendar
│       └── notifications_router.py # GET /notifications + PATCH /notifications/{id}
│
├── migrations/                 # Alembic migrations (baseline schema)
├── frontend/                   # THE FRONTEND (React 18 SPA, JavaScript)
│   └── src/
│       ├── main.jsx            #   React entry; wraps QueryClientProvider + BrowserRouter
│       ├── App.jsx             #   routes (public + protected)
│       ├── index.css           #   Tailwind + span-flash animation
│       ├── lib/                #   api client, query client, zod schemas, formatters
│       ├── store/auth.js       #   Zustand auth store
│       ├── hooks/              #   TanStack Query hooks (progress, analysis, tasks, etc.)
│       ├── components/         #   reusable UI (TaskCard, GroundedSpanText, …)
│       └── pages/              #   route pages (Login, Dashboard, Upload, …)
│
├── .github/workflows/ci.yml    # OPTIONAL CI (no Docker)
├── DEPLOY.md                   # Local run instructions (Windows prerequisites, etc.)
├── requirements.txt            # ML package deps
└── requirements-backend.txt    # Web/backend deps
```

The single most important structural rule: **the `app/` package is completely separate from the `clauseops/` package.** `clauseops` is the brain (the ML); `app` is everything that turns it into a product. `app` *imports* `clauseops` only inside the heavy worker.

---

## 6. The Database (Data Layer)

Location: `app/data/`. PostgreSQL, managed by async SQLAlchemy 2.0 + Alembic.

### 6.1 The seven tables

```
users ─────< contracts ─────< clauses ─────< tasks ─────< reminders
   │              │                            │
   │              └────────────────────────────┘ (tasks also belong to a contract)
   ├────< notifications
   └────< audit_log
```

| Table | Purpose | Key columns |
|---|---|---|
| `users` | An account | `id`, `email` (unique), `password_hash` (Argon2 only) |
| `contracts` | One uploaded PDF + its processing state | `user_id`, `file_key`, `status` (PENDING/PROCESSING/COMPLETE/FAILED), `progress_pct`, `error_message` |
| `clauses` | A segmented, classified unit of a contract | `contract_id`, `clause_index`, `heading`, `clause_type`, `body_text`, `confidence` |
| `tasks` | An obligation derived from a clause (1:1 with the pipeline's `TaskRecord`) | `contract_id`, `clause_id`, `obligated_party`, `action`, **`agent_start/agent_end/action_start/action_end`** (span offsets), `due_date`, `priority`, `status`, `requires_review`, `source_text`, `corrected_fields` (JSONB), `is_user_corrected` |
| `reminders` | A scheduled prompt for a task | `task_id`, `remind_at`, `channel` (email/in_app), `sent`, `sent_at` |
| `notifications` | An in-app message | `user_id`, `task_id`, `message`, `read` |
| `audit_log` | Append-only record of task mutations | `user_id`, `entity`, `entity_id`, `action`, `before` (JSONB), `after` (JSONB) |

### 6.2 Ownership model (important!)

- `user_id` lives **directly** on `contracts`, `notifications`, and `audit_log`.
- `clauses`, `tasks`, and `reminders` are owned **transitively** — they have no `user_id` of their own; they belong to a contract, which belongs to a user. To scope these to a user you **join through `contracts.user_id`**.

This keeps the schema normalized and is enforced consistently by the ownership helpers (see [§8](#8-multi-tenant-ownership-scoping)).

### 6.3 Validation rules (defense in depth)

Rules are enforced at **two layers** — the ORM (`@validates` raising `ValueError` on assignment) and the database (`CheckConstraint` raising `IntegrityError` on flush):

- `users.email` non-empty and unique.
- `contracts.progress_pct` is an integer in `[0, 100]`.
- `contracts.error_message` is non-null **if and only if** `status == FAILED` (a biconditional CHECK).
- `clauses.confidence`, `tasks.confidence`, `tasks.agent_score` are floats in `[0, 1]`.
- `reminders.channel ∈ {email, in_app}` (stored as the lowercase values).
- `reminders.sent_at` is populated only when `sent` is true.

**Why two layers?** The ORM validators give fast, friendly errors during normal use; the DB constraints are the ultimate guarantee that no bad row can ever exist, even if something bypasses the ORM.

### 6.4 Migrations

`migrations/` holds the Alembic environment wired to the app's async engine. The baseline migration creates all 7 tables, indexes (including `user_id` indexes and `tasks.due_date`), FK cascade rules (`ON DELETE CASCADE` so deleting a contract tears down its clauses/tasks/reminders/notifications; `SET NULL` for `tasks.clause_id`), the enum types, and all CHECK constraints. Run with `alembic upgrade head`.

---

## 7. Authentication & Security

### 7.1 The auth core — `app/auth/security.py`

This module is **framework-agnostic** (it knows nothing about FastAPI, the DB, or cookies) so it can be reused by the web layer, the worker, and tests. It provides:

- `hash_password(pw)` / `verify_password(pw, hash)` — **Argon2** via passlib. Only the hash is ever stored; raw passwords never persist.
- `issue_access_token(user_id)` — a short-lived (15 min) **access token** signed with `jwt_secret`, carrying the user id in `sub`, a `type: "access"` claim, and a unique `jti`.
- `issue_refresh_token(user_id)` — a long-lived (14 day) **refresh token** signed with a *separate* `jwt_refresh_secret`, with its own `jti`.
- `decode_access_token` / `decode_refresh_token` — verify signature, expiry, and `type`; raise `TokenError` on anything invalid.
- `rotate_refresh_token(token)` — verifies a refresh token and mints a **new** access+refresh pair, returning the *previous* `jti` so the caller can revoke it (refresh rotation).

**Why two secrets and a `type` claim?** So an access token can never be used where a refresh token is expected (and vice versa), even if leaked.

### 7.2 The HTTP layer — `app/web/auth_router.py`, `dependencies.py`, `revocation.py`

Endpoints:

| Endpoint | Behavior |
|---|---|
| `POST /auth/register` | Creates a user storing only the Argon2 hash; duplicate email → `409`, no user created. |
| `POST /auth/login` | Verifies credentials; on success returns `{access_token, refresh_token}` **and** sets the refresh token in an **httpOnly cookie**. Bad credentials → `401`, no tokens. |
| `POST /auth/refresh` | Reads the refresh cookie, rotates it (new access + refresh), and **revokes the old refresh `jti`**. |
| `POST /auth/logout` | Revokes the current refresh token (`204`). |
| `GET /me` | Returns the current user. |

- The **current-user dependency** (`get_current_user_id` / `get_current_user` in `dependencies.py`) reads the `Authorization: Bearer <access>` header, decodes it, and yields the `user_id`. Missing/invalid → `401`. Every data endpoint depends on this.
- **Refresh-token revocation** (`revocation.py`) is an `InMemoryRefreshTokenStore` behind a `RefreshTokenStore` interface. For the single-process offline MVP an in-memory set of revoked `jti`s is sufficient; the interface lets a Redis-backed store slot in later. Logout and rotation both revoke the relevant `jti` so a stolen/old refresh token can't be replayed.

### 7.3 Security hardening summary

- All data endpoints require a valid JWT and are scoped to `user_id`.
- Upload hardening: size cap, MIME + magic-byte check, randomized object keys, files stored outside the web root.
- Argon2 hashing; short-lived access token + rotating refresh token in an httpOnly cookie.
- The ML worker makes no outbound network calls, so contract data never leaves the host.

---

## 8. Multi-Tenant Ownership Scoping

Location: `app/data/ownership.py`. This is the single source of truth for "who can see/touch what."

**What it provides:**
- **Scoped SELECT builders** — `contracts_for_user(uid)`, `notifications_for_user(uid)`, `audit_logs_for_user(uid)` (direct `user_id`), and `clauses_for_user(uid)`, `tasks_for_user(uid)`, `reminders_for_user(uid)` (join through the parent contract). Each returns a SQLAlchemy `Select` already constrained to that user, which callers extend with their own filters/ordering/pagination.
- **Ownership guards** — `get_owned_or_none(session, Model, id, uid)` returns the entity only if owned (else `None`), and `require_owned(...)` raises `OwnershipError` if missing or not owned.

**Why centralize this?** Cross-tenant data leakage is the highest-severity bug class in a multi-user app. By forcing every list/read/mutation through these helpers, no route can accidentally forget a `WHERE user_id = ...` clause. The ownership predicate is part of the SQL `SELECT`, so a non-owned row is **never read or mutated**.

**The uniform 404 convention:** `OwnershipError` deliberately does **not** distinguish "doesn't exist" from "exists but isn't yours." `app/web/main.py` maps it to a uniform `404 {"detail": "Not found"}`. This prevents attackers from enumerating other users' contract/task IDs.

This behavior is proven by **Property 3** (ownership scoping) — a Hypothesis test that generates arbitrary multi-user datasets and asserts no cross-tenant bleed (see [§16](#16-correctness-properties--testing-strategy)).

---

## 9. Storage Abstraction

Location: `app/storage/`.

- `base.py` defines the abstract `StorageBackend` with four methods: `put(key, bytes)`, `get(key)`, `delete(key)`, `url(key)`. A single `ObjectNotFoundError` is raised for missing keys.
- `local.py` is `LocalFilesystemStorage`: stores objects as files under a configured root **outside the web root**, maps keys to paths, blocks path-traversal (`../`) keys, treats deleting a missing key as a no-op (idempotent), and returns a `file://` URI from `url()`.
- `__init__.py` exposes `get_storage_backend()` which picks the implementation from settings (`local` for the MVP).

**Why an interface?** All file access in the platform flows through these four methods. To move to R2/Supabase later, you write one new class and change configuration — **zero calling code changes.**

---

## 10. The Asynchronous ML Processing Pipeline

This is the heart of the system. Location: `app/processing/`.

### 10.1 Why background processing at all?

Analyzing a contract runs Docling + spaCy + transformers + torch — ~40 seconds and ~1.5–2 GB RAM. You cannot do that inside an HTTP request. So:
- The upload endpoint returns **immediately** with `202 { contract_id, job_id }` and a `PENDING` contract.
- A **Celery worker** picks up the job and does the heavy work in the background.
- The browser watches progress over a WebSocket.

### 10.2 The Celery app — `celery_app.py`

- Creates the Celery app using Redis as **both** broker and result backend.
- Defines **two queues**: `default` (light tasks like the reminder scan) and `ml` (the heavy pipeline). `task_routes` sends anything under the `app.processing.ml.*` namespace to the `ml` queue.
- **Warm-load scaffolding:** `get_pipeline()` is a process-cached accessor that lazily imports the real `clauseops` callables once per worker process. A `worker_process_init` hook (gated by the `CLAUSEOPS_ML_WORKER=1` env var) warm-loads the models so they're resident before the first task — and the cost is amortized across all contracts that worker handles. Importing the module never loads models and never needs Redis.

The real `clauseops` entry points that get wrapped:
- `clauseops.segmentation.segment_contract`
- `clauseops.clause_classification.classifier.classify_clauses`
- `clauseops.entity_extraction.extractor.extract_entities_from_contract`
- `clauseops.obligation_detection.deontic_classifier.classify_contract_obligations`
- `clauseops.obligation_detection.date_normalizer.normalize_contract_dates`
- `clauseops.obligation_detection.task_generator.generate_tasks_for_contract`

### 10.3 The task chain — `ml.py`

`process_contract(contract_id)` is the `ml`-queue entry point. It runs:

```
extract → segment → classify → ner → obligations → normalize_dates → generate_tasks
        → compute span offsets → persist → status COMPLETE   (or FAILED on any error)
```

Step by step:
1. **Mark PROCESSING.** Sets `contracts.status = PROCESSING` (so the UI immediately reflects the running state).
2. **Extract.** Streams the stored PDF out of the `StorageBackend` to a temp file (no network).
3. **Run the chain** via `run_pipeline()`, calling the warm-loaded `clauseops` callables in order. After each stage it fires a **progress hook** (see [§11](#11-live-progress-websocket--redis)).
4. **Compute span offsets** (`_compute_span_offsets_seam` → `spans.py`). For each generated task, locate the `obligated_party` and `action` strings inside `source_text` via `source_text.find(...)` and store `agent_start/agent_end` / `action_start/action_end`. If a string isn't a verbatim substring (e.g. the party was cleaned), the offset is left **null** — *never fabricated*.
5. **Persist + COMPLETE** (`_persist_result_seam`). In a single transaction it writes the clauses, tasks (with offsets + the resolved verbatim `action`), and reminders — all scoped to the owning user via the contract — flags uncertain tasks (`requires_review`), then sets `status = COMPLETE`, `progress_pct = 100`, `completed_at = now`.
6. **On any stage error → FAILED.** The `except` block sets `status = FAILED`, populates `error_message`, publishes the failure, and persists **no partial artifacts as COMPLETE** (the persistence + COMPLETE happen together in one transaction, so reaching the error path means nothing was committed).

**DB access inside Celery:** the app uses *async* SQLAlchemy, but Celery prefork workers run synchronously, so each DB touch uses `asyncio.run()` with a short-lived async engine created and disposed per call (avoids sharing a connection pool across event loops).

### 10.4 The "no guessed deadlines" rule — `spans.py` + `ml.py`

Some deadlines are **relative or conditional** ("within 30 days", "upon completion of Phase 2") and can't be resolved to a concrete calendar date. In that case the task is persisted with `requires_review = true` and `due_date = null` — the system **never invents an anchor date**. This is proven by **Property 8**.

### 10.5 Real `clauseops` facts that shaped the code

- The pipeline's `TaskRecord` has **no `action` field** — the verbatim action lives on the `ObligationRecord`. The span seam correlates each task back to its obligation to recover the exact action string (and computes the offset against that same string, so the round-trip always holds).
- `TaskRecord.source_text` is truncated to `body_text[:500]`, so an offset can legitimately be null when the span falls outside the stored text. The code persists `null` rather than guessing.

---

## 11. Live Progress (WebSocket + Redis)

Two cooperating pieces: the **publisher** (worker side) and the **channel** (web side).

### 11.1 Publisher — `app/processing/progress.py`

- `contract_progress_channel(id)` → the Redis channel name `contract:progress:{id}` (a single shared convention).
- `make_progress_hook(contract_id)` returns a `ProgressHook` that, after each pipeline stage, (a) publishes JSON `{stage, progress_pct, status}` to the contract's Redis channel **and** (b) updates `contracts.progress_pct`/`status` in the DB.
- `progress_pct` is always clamped/rounded to an **int in [0, 100]** by `_clamp_pct` — which is hardened against junk input (non-numeric, NaN, ±infinity) so it can never crash the pipeline. This is proven by **Property 7**.
- Publishing is best-effort (a Redis hiccup won't crash a good run), but the DB update still happens. On failure, `publish_failed(...)` broadcasts a `FAILED` message.

### 11.2 Channel — `app/web/progress_router.py`

- `WS /ws/contracts/{id}?token=<access-jwt>` — a WebSocket endpoint. Because browsers can't set arbitrary headers on a WS handshake, the access token is passed as a **query parameter**. The endpoint:
  1. **Authenticates** the token (invalid/missing → close with code `4401`, *before* accepting).
  2. **Enforces ownership** of the contract (not owned → close with `4403`, *before* subscribing).
  3. Only then **accepts**, subscribes to the contract's Redis channel, and forwards each `{stage, progress_pct, status}` message to the browser until a terminal status (COMPLETE/FAILED) or the client disconnects.
- `GET /contracts/{id}/status` — the **polling fallback**: returns the contract's current `{status, progress_pct}` for clients that can't use a WebSocket (or when the socket drops).

**Why a fallback?** WebSockets can fail behind some proxies/networks. The frontend's `useContractProgress` hook prefers the socket and automatically falls back to polling, so progress always shows.

---

## 12. The Web API (All Endpoints)

All routers live in `app/web/`, mounted by `create_app()` in `main.py`. Every data endpoint requires a valid access token and is scoped to the current user.

### Auth (`auth_router.py`)
- `POST /auth/register` · `POST /auth/login` · `POST /auth/refresh` · `POST /auth/logout` · `GET /me`

### Contracts (`contracts_router.py`)
- `POST /contracts` — multipart upload. Validates ≤20 MB, `application/pdf` MIME, and `%PDF` magic bytes; stores under a randomized key; creates a `PENDING` contract; enqueues processing; returns `202 {contract_id, job_id}`. Invalid → `4xx`, no record, nothing stored.
- `GET /contracts` — paginated list of the user's contracts, optional `status` filter, newest-first.
- `GET /contracts/{id}` — the **full analysis**: contract + its clauses + its tasks (including `source_text` and span offsets). 404 if not owned.
- `DELETE /contracts/{id}` — deletes the contract, cascade-deletes clauses/tasks/reminders/notifications, removes the stored file. 404 if not owned.

### Progress (`progress_router.py`)
- `WS /ws/contracts/{id}` · `GET /contracts/{id}/status`

### Tasks (`tasks_router.py`)
- `GET /tasks` — the user's tasks, filterable by `priority`, `status`, `due_before`, `due_after`, `requires_review`, `contract_id`; deadline-first ordering; pagination.
- `PATCH /tasks/{id}` — change `status` (must be one of PENDING/DONE/SNOOZED/DISMISSED — invalid is rejected and the task is left unchanged) and/or correct fields (recorded in `corrected_fields`, sets `is_user_corrected`). Writes exactly one `audit_log` entry per applied mutation.

### Review queue (`review_router.py`)
- `GET /review-queue` — the user's tasks where `requires_review` is true.

### Dashboard & calendar (`dashboard_router.py`)
- `GET /dashboard/summary` — counts by priority/status, `requires_review_count`, and upcoming deadlines.
- `GET /calendar?from=&to=` — the user's tasks whose `due_date` is in the inclusive window (inverted window → `400`).

### Notifications (`notifications_router.py`)
- `GET /notifications` — the user's notifications, newest-first.
- `PATCH /notifications/{id}` — mark read.

**Schemas** (`schemas.py`) define the Pydantic request/response shapes (`ContractSummary`, `ContractDetail`, `ClauseRead`, `TaskRead`, `DashboardSummary`, `NotificationRead`, etc.), all with `from_attributes=True` so they serialize ORM objects directly.

---

## 13. Reminders & Notifications

- **Reminder scheduler** (`app/processing/reminders.py`): a Celery Beat job (`scan_reminders`) runs roughly every 15 minutes on the **light `default` queue**. It selects reminders where `remind_at <= now` AND `sent == False`, resolves the owner (reminder → task → contract → user), creates an **in-app** Notification, and marks the reminder `sent = true` with `sent_at`.
- **Single delivery / idempotence:** delivery uses a *guarded* `UPDATE ... WHERE id = :id AND sent = false`; the Notification is created only if that UPDATE actually claimed the row. So running the scan any number of times delivers each reminder **at most once** — proven by **Property 5**.
- **In-app only:** the MVP delivers via the `notifications` table; the `email` channel exists in the schema but is reserved for the deferred email integration.

---

## 14. The Frontend (React SPA)

Location: `frontend/`. React 18 + Vite, JavaScript/JSX.

### 14.1 Bootstrapping & shared infrastructure (`src/lib`, `src/main.jsx`, `src/App.jsx`)

- `main.jsx` mounts React and wraps the app in `QueryClientProvider` (TanStack Query) and `BrowserRouter`.
- `lib/api.js` — the HTTP client. It prepends `/api` to every request, attaches the JWT access token as a `Bearer` header, sends `credentials: 'include'` (for the refresh cookie), and on a `401` for a protected call performs **one** silent refresh + retry (then logs out if that fails). It also exposes `contractProgressSocketUrl()` / `openContractProgressSocket()` for the `/ws` endpoint.
- The **Vite proxy** maps `/api/*` → backend root (stripping `/api`) and `/ws` → backend WebSocket. So the SPA calls `/api/auth/login` and it reaches the backend's `/auth/login`.
- `lib/queryClient.js` (shared query client), `lib/authSchemas.js` (Zod login/register schemas), `lib/taskFormat.js` (date/priority/status formatting helpers).

### 14.2 Auth state — `src/store/auth.js` (Zustand)

Holds `user`, `accessToken`, `isAuthenticated`, `loading`, `initialized`. Actions: `login`, `register`, `logout`, `refresh`, `loadMe`, `initialize`. On app start, `initialize()` attempts a **silent refresh** (via the httpOnly cookie) then `loadMe()` to restore a session. It keeps the API client's bearer token in sync.

### 14.3 Routing & protection (`App.jsx`, `components/ProtectedRoute.jsx`)

Public routes: `/login`, `/register`. Protected routes (wrapped in `ProtectedRoute`, which waits for the initial silent-refresh to settle before deciding):
`/` (Dashboard), `/upload`, `/tasks`, `/calendar`, `/review`, `/notifications`, `/contracts/:id`.

### 14.4 Pages (`src/pages`)

| Page | What it does |
|---|---|
| `Login.jsx` / `Register.jsx` | Forms (react-hook-form + Zod); map server errors (409 duplicate, 401 bad creds) to friendly messages. |
| `Upload.jsx` | Drag-drop/file input with client-side validation (PDF, ≤20 MB), submits multipart, then shows live progress. |
| `Dashboard.jsx` | Deadline-first home: counts by priority/status, a prominent "needs review" indicator, upcoming deadlines. |
| `Tasks.jsx` | Filterable task list consuming `GET /tasks`. |
| `Calendar.jsx` | Date-window view over `GET /calendar`, grouped by due date. |
| `ReviewQueue.jsx` | The `requires_review` tasks with amber/dashed emphasis. |
| `ContractAnalysis.jsx` | The grounded source-span viewer (see [§15](#15-the-signature-feature-grounded-source-span-viewer)). |
| `Notifications.jsx` | The notifications panel. |

### 14.5 Hooks (`src/hooks`)

- `useContractProgress(id)` — prefers the WebSocket; on failure falls back to polling `GET /contracts/{id}/status`. Returns `{stage, progressPct, status, isComplete, isFailed, ...}`.
- `useContractAnalysis(id)` — fetches `GET /contracts/{id}` (cached under `['contract-analysis', id]`).
- `useTaskQueries.js` — `useDashboardSummary`, `useTasks(filters)`, `useCalendar`, `useReviewQueue`.
- `useTaskMutation(id)` — `PATCH /tasks/{id}` and invalidates the task/contract/dashboard/calendar queries so views refresh.
- `useNotifications()` — polls `GET /notifications`, exposes `unreadCount`; plus a mark-as-read mutation.

### 14.6 Components (`src/components`)

Reusable building blocks: `Header` (nav + logout), `Field` (accessible input), `GroundedSpanText` + `SpanLegend` + `TaskCard` (the viewer), `TaskRow` + `TaskBadges` + `TaskFilters` (list UI), `TaskStatusControl` + `TaskFieldCorrection` (mutations), `NotificationsPanel`, `ContractProgress`, `ProtectedRoute`, `StateBlock` (loading/error/empty states).

---

## 15. The Signature Feature: Grounded Source-Span Viewer

This is the visible payoff of "trust-but-verify."

**The guarantee:** because the ML pipeline produces parties/actions that are *verbatim substrings* of the clause, the backend stores character offsets such that `source_text[agent_start:agent_end] == obligated_party` and `source_text[action_start:action_end] == action`. This is **Property 1 (grounding round-trip)**.

**How the UI uses it** (`GroundedSpanText.jsx`):
- `buildSegments(text, spans)` splits the source text at every span boundary and assigns each slice the highest-priority covering kind (party > action > deadline), guaranteeing the concatenation of segments equals the original text exactly. Overlapping/adjacent/null/out-of-bounds ranges are all handled gracefully.
- Each highlighted range is wrapped in a `<mark>` with a distinct color (party = blue, action = green, deadline = amber). A `SpanLegend` explains the colors. `TaskCard` distinguishes PROHIBITION vs OBLIGATION with a border/badge and gives `requires_review` tasks a dashed-amber treatment.

**Interactions** (`ContractAnalysis.jsx`):
- Clicking a **task card** scrolls to and **flashes** its source span (a brief CSS animation, softened for `prefers-reduced-motion`).
- Clicking a **highlighted span** opens/activates the associated task.
- Spans and cards are keyboard-accessible (focusable, Enter/Space activates) with ARIA labels.

> The backend currently persists party and action offsets (not a deadline offset). The viewer takes an optional deadline range and will highlight it automatically the moment the backend provides one — no frontend change needed.

---

## 16. Correctness Properties & Testing Strategy

The project uses three test styles:
- **Unit tests** — specific behaviors and edge cases (auth flows, upload validation, dashboard aggregation, etc.).
- **Integration tests** (`app/processing/test_integration.py`) — the full upload → chain → persisted analysis flow against a real (SQLite) database with the heavy ML pipeline **stubbed** (so CI stays fast). Verifies persistence, ownership scoping, span round-trip, status transitions, and the FAILED path.
- **Property-based tests** (Hypothesis) — universal invariants over generated inputs. These encode the design's **9 Correctness Properties**:

| # | Property | What it guarantees | Validates |
|---|---|---|---|
| 1 | Grounding round-trip | Stored offsets recover the exact extracted party/action | Req 5.2 |
| 2 | Span-offset bounds | `0 ≤ start ≤ end ≤ len(source_text)` for every span | Req 5.3 |
| 3 | Ownership scoping | Queries return only the owner's rows; non-owned refs denied with no read/mutation | Req 2.1, 2.2 |
| 4 | Task-status validity | Status always ∈ {PENDING,DONE,SNOOZED,DISMISSED}; bad mutation rejected, task unchanged | Req 7.1, 7.2 |
| 5 | Reminder single-delivery | The scan delivers each reminder at most once | Req 9.2, 9.3 |
| 6 | Audit completeness | Every applied mutation writes exactly one audit entry; no-ops write none | Req 7.4 |
| 7 | Progress bounds | Every published/stored `progress_pct` is an int in [0,100] | Req 4.2 |
| 8 | No guessed deadlines | Unresolved relative/conditional deadline → `requires_review` true and `due_date` null | Req 8.2 |
| 9 | Contract-status domain | Status always valid; `error_message` non-null iff FAILED | Req 4.3, 4.6, 4.7 |

The full backend suite is **251 tests passing**. (A real robustness bug — non-finite progress values like `'INF'` crashing the clamp — was caught by Property 7 during final verification and fixed.)

---

## 17. End-to-End Walkthrough

Here is the complete life of a contract, tying every layer together.

1. **Sign up / log in.** The user registers (`POST /auth/register`) and logs in (`POST /auth/login`). The browser receives an access token (kept in the Zustand store) and a refresh token (httpOnly cookie). Every subsequent API call carries the access token; if it expires, the API client silently refreshes once.

2. **Upload.** On `/upload`, the user picks a PDF. The frontend validates it (PDF, ≤20 MB) and POSTs multipart to `/api/contracts`. The backend re-validates (size, MIME, `%PDF` magic bytes), stores the bytes under a randomized key via `StorageBackend`, creates a `contracts` row with `status=PENDING`, **enqueues** `process_contract(contract_id)` on the Celery `ml` queue, and returns `202 {contract_id, job_id}`.

3. **Watch progress.** The frontend opens `WS /ws/contracts/{id}?token=...`. The endpoint authenticates and checks ownership, then subscribes to the contract's Redis channel.

4. **The worker runs.** A Celery `ml` worker (with warm-loaded models) picks up the job: sets `PROCESSING`, extracts the PDF from storage, and runs the `clauseops` chain (segment → classify → ner → obligations → normalize_dates → generate_tasks). After each stage it publishes `{stage, progress_pct, status}` to Redis — which the API forwards to the browser, so the progress bar moves in real time.

5. **Grounding + persistence.** The worker computes span offsets for each task (so they round-trip to the verbatim text), then in one transaction writes the clauses, tasks (with offsets, the resolved `action`, `requires_review` flags, and any reminders) all scoped to the owning user, sets `status=COMPLETE`, `progress_pct=100`. If any stage threw, it sets `FAILED` with an `error_message` and persists nothing as COMPLETE. The terminal status is published; the WebSocket closes.

6. **Explore the analysis.** The browser navigates to `/contracts/{id}`, which calls `GET /contracts/{id}` and renders the **grounded viewer**: each clause with its tasks, the exact party/action highlighted in the source text, color legend, prohibition/obligation badges, and amber dashed treatment for review items.

7. **Work the deadlines.** The **Dashboard** (`/`) shows counts, the review count, and upcoming deadlines. The **Task list** (`/tasks`) filters by priority/status/dates/contract. The **Calendar** (`/calendar`) windows by date. The **Review queue** (`/review`) shows uncertain items.

8. **Mutate with audit.** The user changes a task's status or corrects a field via `PATCH /tasks/{id}`. The backend validates the status, records field overrides in `corrected_fields`, sets `is_user_corrected`, and writes exactly one `audit_log` row capturing before/after. The frontend invalidates its caches so every view refreshes.

9. **Reminders.** Celery Beat runs the reminder scan (~15 min). Due, unsent reminders create in-app Notifications for the owner and are marked sent (exactly once). The user sees them in the **Notifications** panel and marks them read (`PATCH /notifications/{id}`).

10. **Cleanup.** Deleting a contract (`DELETE /contracts/{id}`) cascade-removes its clauses/tasks/reminders/notifications and deletes the stored file.

---

## 18. Major Design Decisions (and the reasoning)

- **Wrap, never rewrite `clauseops`.** The ML pipeline is mature; the project's job is to productize it. The worker imports it; nothing else touches it. This isolates risk and keeps the ML team's code authoritative.
- **Heavy work on an isolated `ml` queue with warm models.** Keeps the API responsive and prevents ~40 s jobs from blocking light tasks; loading models once per process amortizes their cost.
- **Offline-first behind thin interfaces.** Storage, auth identity, and (future) email sit behind interfaces driven by environment config, so managed services can be added later with no rewrite. No Firebase; runs entirely on local services (no Docker).
- **Own JWT with rotating refresh + httpOnly cookie.** Secure session handling without an external identity provider, with refresh rotation + revocation to limit token replay.
- **Two-layer validation (ORM + DB constraints).** Friendly errors plus an ironclad guarantee no invalid row can exist.
- **Centralized ownership scoping + uniform 404.** Eliminates the cross-tenant leakage bug class and avoids ID enumeration.
- **Grounded spans computed by `find()` with null-on-miss.** Honors trust-but-verify: highlights are exact or absent, never guessed.
- **Property-based tests for the invariants that matter.** The 9 properties encode the product's safety guarantees and are checked across thousands of generated cases.
- **JavaScript frontend (not TypeScript).** A locked stack decision; the SPA leans on Zod for runtime validation at the edges.

---

## 19. How to Run the Project

> ClauseOps is offline-first and runs entirely on local services. **There is no Docker in this project.** See `DEPLOY.md` for the same instructions in condensed form.

### Prerequisites
- **Python 3.12** (a `venv/` already exists in the repo)
- **Node.js 20+**
- **PostgreSQL** running on `localhost:5432`
- **Redis** running on `localhost:6379`

**Installing PostgreSQL / Redis on Windows:**
- PostgreSQL: use the official Windows installer, then create the role/db that matches the default connection string:
  ```sql
  CREATE ROLE clauseops WITH LOGIN PASSWORD 'clauseops';
  CREATE DATABASE clauseops OWNER clauseops;
  ```
- Redis: Redis has **no official native Windows build**, so run the real Redis binary inside **WSL** (recommended):
  ```bat
  wsl --install            :: one-time (installs Ubuntu); reboot if prompted
  ```
  then in the Ubuntu shell:
  ```bash
  sudo apt update && sudo apt install redis-server
  sudo service redis-server start
  redis-cli ping           # -> PONG
  ```
  WSL2 forwards localhost, so the Windows-side app reaches it at `localhost:6379` with no config change. (A Redis-compatible Windows alternative such as **Memurai** also works if you'd rather not use WSL.)

> Different hosts/credentials? Set `CLAUSEOPS_DATABASE_URL` and `CLAUSEOPS_REDIS_URL` (see `app/config.py`). Defaults: DB `postgresql+asyncpg://clauseops:clauseops@localhost:5432/clauseops`, Redis `redis://localhost:6379/0`, storage root `~/.clauseops/storage`.

### 1. Install dependencies
```bat
venv\Scripts\python.exe -m pip install -r requirements-backend.txt
venv\Scripts\python.exe -m pip install -r requirements.txt
```
> `requirements.txt` pulls the heavy ML stack (Docling, spaCy, transformers, torch) needed only by the ML worker. You can skip it if you only want the API + frontend.

### 2. Apply database migrations
```bat
venv\Scripts\alembic.exe upgrade head
```

### 3. Start the services

> **Windows note:** Celery's default `prefork` pool crashes on Windows with `PermissionError: [WinError 5] Access is denied`. Always pass `--pool=solo` to worker commands on Windows.

**Terminal 1 — API**
```bat
venv\Scripts\uvicorn.exe app.web.main:app --reload
```

**Terminal 2 — Celery worker (both queues)**

PowerShell (prompt `PS C:\...>`):
```powershell
$env:CLAUSEOPS_ML_WORKER='1'
venv\Scripts\celery.exe -A app.processing.celery_app worker -Q ml,default --pool=solo --loglevel=info
```
Command Prompt (cmd):
```bat
set CLAUSEOPS_ML_WORKER=1
venv\Scripts\celery.exe -A app.processing.celery_app worker -Q ml,default --pool=solo --loglevel=info
```
- `-Q ml,default` → this single worker handles both the heavy contract processing and the light reminder tasks.
- `--pool=solo` → required on Windows.

> **Don't mix shells.** In PowerShell, `set X=1` does NOT set an env var and `&&` is not a valid separator — use the `$env:` form. The embedded-beat flag `-B` is **not supported on Windows**; if you need the periodic reminder scan, run Beat as a separate process (Terminal 4 below).

**Terminal 3 — frontend** (run `npm install` once, then just `npm run dev`)
```bat
cd frontend
npm install
npm run dev
```
The Vite dev server proxies `/api` and `/ws` to the API at `http://localhost:8000`. Open the URL Vite prints (typically `http://localhost:5173`).

**Terminal 4 — Celery Beat** (OPTIONAL — only for periodic reminders)
```bat
venv\Scripts\celery.exe -A app.processing.celery_app beat --loglevel=info
```

> In production (Linux) you'd split the worker into separate `default`/`ml` workers, run Beat standalone, and drop `--pool=solo`.

> **Tip:** For normal dev you can skip Beat (Terminal 4). To click around without real PDF processing, skip the worker (Terminal 2) too — uploads stay `PENDING`, but auth/dashboard/etc. all work (a 2-terminal setup).

### Running the tests
```bat
:: Full backend suite (unit + integration + property-based)
venv\Scripts\python.exe -m pytest app/ tests/storage -q

:: Frontend build (compile check)
cd frontend && npm run build
```

---

## 20. Glossary

- **Contract** — one uploaded PDF and its processing status (PENDING/PROCESSING/COMPLETE/FAILED).
- **Clause** — a segmented, classified unit of a contract.
- **Task** — an obligation derived from a clause (1:1 with the pipeline's `TaskRecord`); statuses PENDING/DONE/SNOOZED/DISMISSED.
- **Grounding invariant** — the guarantee that a task's stored offsets recover the exact extracted party/action substring of `source_text`.
- **Source span / offsets** — the `agent_start/agent_end` and `action_start/action_end` character ranges into `source_text`.
- **requires_review** — a flag on a task marking an uncertain extraction (inferred party, unresolved relative/conditional deadline).
- **Owner** — the authenticated user whose `user_id` scopes a contract/task/notification.
- **`ml` queue** — the dedicated heavy Celery queue that runs the `clauseops` pipeline with warm models.
- **Progress channel** — the Redis pub/sub channel (`contract:progress:{id}`) the worker publishes to and the WebSocket forwards.
- **Audit log** — append-only before/after record of every applied task mutation.

---

*This guide reflects the codebase after all four development phases (data layer & auth → upload, worker & read APIs → mutations, audit & reminders → frontend, integration & optional ops tooling). For the formal requirements, design, and task breakdown, see `.kiro/specs/clauseops-web-platform/`.*

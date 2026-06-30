import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ApiError } from '../lib/api';
import { useContractAnalysis } from '../hooks/useContractAnalysis';
import SpanLegend from '../components/SpanLegend';
import TaskCard from '../components/TaskCard';

// The Grounded Source-Span Viewer page (Requirements 5.4, 5.5, 5.6).
//
// Fetches a contract's full analysis (contract + clauses + tasks with span offsets) and
// renders, per clause, each task's source text with its party/action/deadline highlights.
// A color legend explains the marks. Loading / error / empty states are handled explicitly.
//
// Interaction (task 18.2): clicking a task card scrolls to and flashes its source span;
// clicking a highlighted span opens (activates) the associated task. Selection state is
// coordinated here so a single task is "active" at a time, with refs for scroll-into-view and
// a transient CSS flash. Motion is softened for users who prefer reduced motion.

function groupTasksByClause(clauses, tasks) {
  const byClause = new Map();
  for (const task of tasks || []) {
    const key = task.clause_id ?? '__unassigned__';
    if (!byClause.has(key)) byClause.set(key, []);
    byClause.get(key).push(task);
  }

  const ordered = (clauses || [])
    .slice()
    .sort((a, b) => (a.clause_index ?? 0) - (b.clause_index ?? 0))
    .map((clause) => ({ clause, tasks: byClause.get(clause.id) || [] }));

  // Surface any tasks whose clause is missing from the payload so nothing is silently lost.
  const orphanTasks = byClause.get('__unassigned__') || [];
  return { ordered, orphanTasks };
}

function StateShell({ children }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
      {children}
    </section>
  );
}

export default function ContractAnalysis() {
  const { id } = useParams();
  const { data, isLoading, isError, error, refetch } = useContractAnalysis(id);

  // --- Span/card interaction coordination (Requirement 5.6) -------------------------------
  // A single task is "active" at a time; `flash` carries a transient highlight whose `nonce`
  // bumps on every activation so the CSS animation replays even on repeat clicks.
  const [selectedTaskId, setSelectedTaskId] = useState(null);
  const [flash, setFlash] = useState({ id: null, nonce: 0 });
  const cardRefs = useRef(new Map());
  const flashTimer = useRef(null);

  const registerCard = useCallback(
    (taskId) => (el) => {
      if (el) cardRefs.current.set(taskId, el);
      else cardRefs.current.delete(taskId);
    },
    [],
  );

  const prefersReducedMotion = () =>
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const scrollToTask = useCallback((taskId) => {
    const el = cardRefs.current.get(taskId);
    if (!el || typeof el.scrollIntoView !== 'function') return;
    el.scrollIntoView({
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
      block: 'center',
    });
  }, []);

  const focusTask = useCallback((taskId) => {
    const el = cardRefs.current.get(taskId);
    if (el && typeof el.focus === 'function') el.focus({ preventScroll: true });
  }, []);

  // Card click: select, scroll into view, and flash the source span.
  const handleCardActivate = useCallback(
    (taskId) => {
      setSelectedTaskId(taskId);
      setFlash((prev) => ({ id: taskId, nonce: prev.nonce + 1 }));
      scrollToTask(taskId);
      focusTask(taskId);
    },
    [scrollToTask, focusTask],
  );

  // Span click: "open" (activate) the associated task — select + scroll, no extra flash so the
  // two interactions stay distinguishable.
  const handleSpanActivate = useCallback(
    (taskId) => {
      setSelectedTaskId(taskId);
      scrollToTask(taskId);
      focusTask(taskId);
    },
    [scrollToTask, focusTask],
  );

  // Clear the flash after the animation window so it can re-trigger and never lingers (which
  // matters most under prefers-reduced-motion, where the highlight is static).
  useEffect(() => {
    if (flash.id == null) return undefined;
    if (flashTimer.current) clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => {
      setFlash((prev) => ({ ...prev, id: null }));
    }, 1400);
    return () => {
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, [flash]);

  if (isLoading) {
    return (
      <StateShell>
        <p className="text-sm text-slate-500" role="status" aria-live="polite">
          Loading contract analysis…
        </p>
      </StateShell>
    );
  }

  if (isError) {
    const notFound = error instanceof ApiError && (error.status === 404 || error.status === 403);
    return (
      <StateShell>
        <h1 className="text-lg font-semibold text-slate-800">
          {notFound ? 'Contract not found' : 'Could not load analysis'}
        </h1>
        <p className="mt-2 text-sm text-slate-500">
          {notFound
            ? 'This contract does not exist or you do not have access to it.'
            : (error && error.message) || 'Something went wrong while loading this contract.'}
        </p>
        {!notFound && (
          <button
            type="button"
            onClick={() => refetch()}
            className="btn-primary mt-4"
          >
            Try again
          </button>
        )}
      </StateShell>
    );
  }

  const contract = data || {};
  const { ordered, orphanTasks } = groupTasksByClause(contract.clauses, contract.tasks);
  const taskCount = (contract.tasks || []).length;
  const reviewCount = (contract.tasks || []).filter((t) => t && t.requires_review).length;
  const datedCount = (contract.tasks || []).filter((t) => t && t.due_date).length;

  return (
    <div className="space-y-5">
      {/* Document header — clean panel, no gradient. Reads like a case/file header. */}
      <header className="card p-5">
        <Link
          to="/contracts"
          className="inline-flex items-center gap-1 text-xs font-medium text-ink-500 transition-colors hover:text-brand-700"
        >
          ← Back to Analysis
        </Link>
        <div className="mt-2 flex items-start gap-3">
          <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-ink-200 bg-ink-50 text-ink-500">
            <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
              <path
                d="M14 3v4a1 1 0 0 0 1 1h4M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <div className="min-w-0">
            <h1 className="break-words text-xl font-semibold leading-tight text-ink-900">
              {contract.filename || 'Contract analysis'}
            </h1>
            <p className="mt-1 text-sm text-ink-500">
              {taskCount === 0
                ? 'No obligations were extracted from this contract.'
                : 'Obligations grounded to the exact clauses they were extracted from.'}
            </p>
          </div>
        </div>

        {/* Metric tiles — bordered cells, tabular figures, one accent number each. */}
        <dl className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-ink-200 bg-ink-200 sm:grid-cols-4">
          <div className="bg-white px-4 py-3">
            <dt className="section-label">Obligations</dt>
            <dd className="tabular mt-1 text-xl font-semibold text-ink-900">{taskCount}</dd>
          </div>
          <div className="bg-white px-4 py-3">
            <dt className="section-label">With deadlines</dt>
            <dd className="tabular mt-1 text-xl font-semibold text-ink-900">{datedCount}</dd>
          </div>
          <div className="bg-white px-4 py-3">
            <dt className="section-label">Needs review</dt>
            <dd
              className={`tabular mt-1 text-xl font-semibold ${
                reviewCount > 0 ? 'text-risk-high' : 'text-ink-900'
              }`}
            >
              {reviewCount}
            </dd>
          </div>
          <div className="bg-white px-4 py-3">
            <dt className="section-label">Status</dt>
            <dd className="mt-1 text-sm font-semibold text-ink-800">
              {contract.status || '—'}
            </dd>
          </div>
        </dl>
      </header>

      <SpanLegend />

      {taskCount === 0 ? (
        <StateShell>
          <p className="text-sm text-slate-500">
            There are no tasks to display yet. If this contract is still processing, the
            analysis will appear here once it completes.
          </p>
        </StateShell>
      ) : (
        <div className="space-y-6">
          {ordered.map(({ clause, tasks }) => (
            <section
              key={clause.id}
              className="card p-5"
              aria-label={clause.heading || `Clause ${clause.clause_index ?? ''}`}
            >
              <header className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-100 pb-3">
                <h2 className="text-base font-semibold text-ink-900">
                  {clause.heading || `Clause ${(clause.clause_index ?? 0) + 1}`}
                </h2>
                {clause.clause_type && (
                  <span className="badge badge-neutral">{clause.clause_type}</span>
                )}
              </header>

              {tasks.length === 0 ? (
                <div className="mt-3 space-y-1.5">
                  <p className="text-xs italic text-slate-400">
                    No obligations extracted from this clause.
                  </p>
                  {clause.body_text ? (
                    <p className="line-clamp-3 break-words text-sm leading-relaxed text-slate-500">
                      {clause.body_text}
                    </p>
                  ) : null}
                </div>
              ) : (
                <div className="mt-4 space-y-4">
                  {tasks.map((task) => (
                    <TaskCard
                      key={task.id}
                      ref={registerCard(task.id)}
                      task={task}
                      isActive={selectedTaskId === task.id}
                      isFlashing={flash.id === task.id}
                      flashNonce={flash.nonce}
                      onActivate={handleCardActivate}
                      onSpanActivate={handleSpanActivate}
                    />
                  ))}
                </div>
              )}
            </section>
          ))}

          {orphanTasks.length > 0 && (
            <section
              className="card p-5"
              aria-label="Other obligations"
            >
              <header className="border-b border-slate-100 pb-3">
                <h2 className="text-base font-semibold text-slate-800">Other obligations</h2>
              </header>
              <div className="mt-4 space-y-4">
                {orphanTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    ref={registerCard(task.id)}
                    task={task}
                    isActive={selectedTaskId === task.id}
                    isFlashing={flash.id === task.id}
                    flashNonce={flash.nonce}
                    onActivate={handleCardActivate}
                    onSpanActivate={handleSpanActivate}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

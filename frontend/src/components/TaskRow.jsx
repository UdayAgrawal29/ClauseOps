import { useState } from 'react';
import { Link } from 'react-router-dom';
import { formatDueDate } from '../lib/taskFormat';
import { PriorityBadge, StatusBadge, ReviewBadge } from './TaskBadges';
import GroundedSpanText from './GroundedSpanText';
import SpanLegend from './SpanLegend';
import DateDerivation from './DateDerivation';
import TaskStatusControl from './TaskStatusControl';
import TaskFieldCorrection from './TaskFieldCorrection';

// Small inline document glyph — replaces the emoji for a consistent, professional mark.
function DocGlyph({ className = 'h-3.5 w-3.5' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden="true">
      <path
        d="M14 3v4a1 1 0 0 0 1 1h4M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * A single task row showing its key fields (title, obligated party, due date, priority,
 * status, review flag) with a link to the originating contract's analysis
 * (Requirements 6.1, 8.3, 8.4).
 *
 * Each row has an inline "Analysis" toggle that expands a per-task panel showing the grounded
 * source span, the task's details, how the deadline was derived, a status control, and a
 * field-correction form — so the user can review/edit a task without leaving the list
 * (Requirements 5.4, 5.5, 7.1, 7.3).
 *
 * @param {{ task: object, showReview?: boolean, contractName?: string }} props
 */
export default function TaskRow({ task, showReview = true, contractName }) {
  const needsReview = Boolean(task.requires_review);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);

  const panelId = `task-analysis-${task.id}`;
  const confidencePct =
    typeof task.confidence === 'number' ? `${Math.round(task.confidence * 100)}%` : null;

  return (
    <article
      className={`overflow-hidden rounded-lg border bg-white shadow-card transition-colors ${
        needsReview
          ? 'border-ink-200 border-l-2 border-l-amber-400'
          : 'border-ink-200 hover:border-ink-300'
      } ${open ? 'ring-1 ring-brand-200' : ''}`}
    >
      {/* Summary row */}
      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-ink-900">
              {task.title || 'Untitled obligation'}
            </h3>
            {contractName ? (
              <p className="mt-1 flex items-center gap-1.5 truncate text-xs text-ink-500">
                <DocGlyph className="h-3.5 w-3.5 shrink-0 text-ink-400" />
                <span className="truncate" title={contractName}>
                  {contractName}
                </span>
              </p>
            ) : null}
            {task.obligated_party ? (
              <p className="mt-1 truncate text-xs text-ink-500">
                <span className="text-ink-400">Party:</span> {task.obligated_party}
              </p>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
            <PriorityBadge priority={task.priority} />
            <StatusBadge status={task.status} />
            {showReview && needsReview ? <ReviewBadge /> : null}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="inline-flex items-center gap-1.5 rounded border border-ink-200 bg-ink-50 px-2 py-1 font-medium text-ink-700">
            <span className="text-2xs uppercase tracking-wide text-ink-400">Due</span>
            <span className="tabular">{formatDueDate(task.due_date)}</span>
          </span>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls={panelId}
            className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 font-semibold transition-colors ${
              open
                ? 'bg-brand-600 text-white hover:bg-brand-700'
                : 'border border-ink-200 text-brand-700 hover:bg-brand-50'
            }`}
          >
            Analysis
            <span aria-hidden="true" className={`transition-transform ${open ? 'rotate-180' : ''}`}>
              ▾
            </span>
          </button>
        </div>
      </div>

      {/* Inline analysis + edit panel for THIS task */}
      {open ? (
        <div
          id={panelId}
          className="animate-fade-in-up space-y-3 border-t border-ink-200 bg-ink-50 p-4"
        >
          {/* Grounded source span (the exact text this obligation came from) */}
          <div className="panel p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="section-label">Source clause</span>
              {confidencePct ? (
                <span className="inline-flex items-center gap-1 rounded border border-ink-200 bg-ink-50 px-1.5 py-0.5 text-2xs font-medium text-ink-600">
                  Confidence <span className="tabular font-semibold">{confidencePct}</span>
                </span>
              ) : null}
            </div>
            <div className="rounded-md bg-ink-50 p-3 ring-1 ring-inset ring-ink-200/70">
              <GroundedSpanText
                sourceText={task.source_text}
                partyStart={task.agent_start}
                partyEnd={task.agent_end}
                actionStart={task.action_start}
                actionEnd={task.action_end}
                deadlineStart={task.deadline_start}
                deadlineEnd={task.deadline_end}
              />
            </div>
            <div className="mt-3">
              <SpanLegend />
            </div>
          </div>

          {/* Details + deadline derivation, side by side on wider screens */}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="panel p-3">
              <span className="section-label mb-2 block">Details</span>
              <dl className="divide-y divide-ink-100 text-sm">
                {task.action ? (
                  <div className="py-1.5 first:pt-0">
                    <dt className="text-xs text-ink-400">Action</dt>
                    <dd className="mt-0.5 font-medium text-span-action">{task.action}</dd>
                  </div>
                ) : null}
                {task.obligation_type ? (
                  <div className="kv-row">
                    <dt className="kv-key">Type</dt>
                    <dd className="kv-val">{task.obligation_type}</dd>
                  </div>
                ) : null}
                {task.beneficiary ? (
                  <div className="kv-row">
                    <dt className="kv-key">Beneficiary</dt>
                    <dd className="kv-val">{task.beneficiary}</dd>
                  </div>
                ) : null}
              </dl>
            </div>

            <DateDerivation task={task} />
          </div>

          {/* Edit controls: status + field corrections (Requirements 7.1, 7.3) */}
          <div className="panel p-3">
            <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
              <div>
                <span className="section-label mb-1 block">Status</span>
                <TaskStatusControl
                  taskId={task.id}
                  status={task.status}
                  contractId={task.contract_id}
                />
              </div>
              <div>
                <span className="section-label mb-1 block">Edit</span>
                <button
                  type="button"
                  onClick={() => setEditing((v) => !v)}
                  className={`rounded-md border px-3 py-1.5 text-sm font-medium transition-colors ${
                    editing
                      ? 'border-brand-300 bg-brand-50 text-brand-700 hover:bg-brand-100'
                      : 'border-ink-300 text-ink-700 hover:bg-ink-50'
                  }`}
                >
                  {editing ? 'Close editor' : 'Correct fields'}
                </button>
              </div>
              {task.is_user_corrected ? (
                <span className="mt-5 inline-flex items-center rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-risk-high">
                  Edited
                </span>
              ) : null}
            </div>
          </div>

          {/* Field-correction form (own section, not nested inside the status card) */}
          {editing ? (
            <TaskFieldCorrection
              task={task}
              contractId={task.contract_id}
              onSaved={() => setEditing(false)}
              onCancel={() => setEditing(false)}
            />
          ) : null}

          {task.contract_id != null ? (
            <div className="pt-0.5">
              <Link
                to={`/contracts/${task.contract_id}`}
                className="text-xs font-medium text-brand-700 underline underline-offset-2 hover:text-brand-800"
              >
                Open full contract analysis →
              </Link>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

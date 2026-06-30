import { ApiError } from '../lib/api';
import { useTaskMutation, TASK_STATUSES } from '../hooks/useTaskMutation';

// Human-friendly labels for each valid status (Requirement 7.1).
const STATUS_LABELS = {
  PENDING: 'Pending',
  DONE: 'Done',
  SNOOZED: 'Snoozed',
  DISMISSED: 'Dismissed',
};

function messageForError(err) {
  if (err instanceof ApiError) {
    // 422 == the server rejected the requested status; the task is left unchanged
    // (Requirement 7.2).
    if (err.status === 422) {
      return "That status isn't allowed. The task was left unchanged.";
    }
    if (err.status === 401) return 'Your session has expired. Please sign in again.';
    if (err.status === 404) return 'This task could not be found.';
    return err.message || 'Could not update the status. Please try again.';
  }
  return 'Something went wrong while updating the status. Please try again.';
}

/**
 * A status dropdown for a single task. Changing the selection issues `PATCH /tasks/{id}` with
 * the new status and, on success, invalidates the task/contract/dashboard caches so dependent
 * views refetch (handled by useTaskMutation).
 *
 * The control is disabled while the mutation is pending and surfaces errors via an
 * aria-live region. An invalid/rejected status (422) leaves the task unchanged and shows a
 * message.
 *
 * @param {{
 *   taskId: string|number,
 *   status: string,
 *   contractId?: string|number,
 *   onUpdated?: (task: object) => void,
 *   className?: string,
 * }} props
 */
export default function TaskStatusControl({
  taskId,
  status,
  contractId,
  onUpdated,
  className = '',
}) {
  const mutation = useTaskMutation(taskId, { contractId, onSuccess: onUpdated });

  // Drive the select from the server-provided status so it reflects the authoritative value
  // after invalidation/refetch. While pending we still show the in-flight selection.
  const selectedValue = mutation.isPending
    ? mutation.variables?.status ?? status
    : status;

  function handleChange(event) {
    const next = event.target.value;
    if (next === status) return;
    mutation.mutate({ status: next });
  }

  const selectId = `task-status-${taskId}`;
  const errorId = `task-status-${taskId}-error`;
  const error = mutation.isError ? messageForError(mutation.error) : null;

  return (
    <div className={className}>
      <label htmlFor={selectId} className="sr-only">
        Task status
      </label>
      <div className="flex items-center gap-2">
        <select
          id={selectId}
          value={selectedValue}
          onChange={handleChange}
          disabled={mutation.isPending}
          aria-busy={mutation.isPending}
          aria-invalid={error ? 'true' : 'false'}
          aria-describedby={error ? errorId : undefined}
          className="rounded-md border border-ink-300 bg-white px-2.5 py-1.5 text-sm text-ink-800 transition-colors focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/25 disabled:cursor-not-allowed disabled:bg-ink-100 disabled:text-ink-400"
        >
          {TASK_STATUSES.map((value) => (
            <option key={value} value={value}>
              {STATUS_LABELS[value] || value}
            </option>
          ))}
        </select>
        {mutation.isPending && (
          <span className="text-xs text-ink-400" aria-hidden="true">
            Saving…
          </span>
        )}
      </div>
      <p id={errorId} role="alert" aria-live="polite" className="mt-1 min-h-[1rem] text-xs text-risk-critical">
        {error || ''}
      </p>
    </div>
  );
}

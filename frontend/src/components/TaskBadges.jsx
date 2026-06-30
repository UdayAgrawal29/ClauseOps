import { priorityBadgeClass, statusBadgeClass } from '../lib/taskFormat';

// Consistent pill badges for a task's priority, status, and review flag. Each renders a
// readable label with screen-reader context.

const basePill =
  'inline-flex items-center rounded border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide';

export function PriorityBadge({ priority }) {
  if (!priority) return null;
  return (
    <span className={`${basePill} ${priorityBadgeClass(priority)}`}>
      <span className="sr-only">Priority: </span>
      {priority}
    </span>
  );
}

export function StatusBadge({ status }) {
  if (!status) return null;
  return (
    <span className={`${basePill} ${statusBadgeClass(status)}`}>
      <span className="sr-only">Status: </span>
      {status}
    </span>
  );
}

// Restrained amber treatment for requires_review tasks (Requirement 8.4).
export function ReviewBadge() {
  return (
    <span className={`${basePill} border-amber-200 bg-amber-50 text-risk-high`}>
      Needs review
    </span>
  );
}

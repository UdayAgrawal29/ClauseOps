// Shared presentation helpers for the deadline-first task UI (dashboard, task list,
// calendar, review queue). Keeping these pure and in one place keeps the badges, sorting,
// and date handling consistent across screens (Requirements 6.1-6.3, 8.3).

// Task priorities, highest urgency first. Mirrors the ML pipeline's priority enum.
export const PRIORITIES = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];

// Task lifecycle statuses (Requirement 7.1).
export const STATUSES = ['PENDING', 'DONE', 'SNOOZED', 'DISMISSED'];

const PRIORITY_RANK = PRIORITIES.reduce((acc, p, i) => {
  acc[p] = i;
  return acc;
}, {});

// Tailwind classes for the priority badge. Muted, professional severity tones (no neon)
// so the most urgent work reads first without alarm-fatigue.
const PRIORITY_BADGE = {
  CRITICAL: 'border-red-200 bg-red-50 text-risk-critical',
  HIGH: 'border-amber-200 bg-amber-50 text-risk-high',
  MEDIUM: 'border-yellow-200 bg-yellow-50 text-risk-medium',
  LOW: 'border-ink-200 bg-ink-50 text-risk-low',
};

const STATUS_BADGE = {
  PENDING: 'border-brand-200 bg-brand-50 text-brand-700',
  DONE: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  SNOOZED: 'border-ink-200 bg-ink-50 text-ink-600',
  DISMISSED: 'border-ink-200 bg-ink-50 text-ink-400',
};

export function priorityBadgeClass(priority) {
  return PRIORITY_BADGE[priority] || 'border-ink-200 bg-ink-50 text-ink-600';
}

export function statusBadgeClass(status) {
  return STATUS_BADGE[status] || 'border-ink-200 bg-ink-50 text-ink-600';
}

export function priorityRank(priority) {
  const rank = PRIORITY_RANK[priority];
  return rank === undefined ? PRIORITIES.length : rank;
}

// Parse a backend date string (YYYY-MM-DD or ISO) into a Date, or null when absent/invalid.
export function parseDueDate(value) {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

// Human-readable due date, e.g. "Mar 14, 2025". Returns a placeholder when unset.
export function formatDueDate(value) {
  const d = parseDueDate(value);
  if (!d) return 'No date';
  return d.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

// Format a Date as YYYY-MM-DD in local time (for date inputs and calendar windows).
export function toISODate(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

// First and last day of the month containing `ref` (defaults to today), as ISO date strings.
export function currentMonthRange(ref = new Date()) {
  const from = new Date(ref.getFullYear(), ref.getMonth(), 1);
  const to = new Date(ref.getFullYear(), ref.getMonth() + 1, 0);
  return { from: toISODate(from), to: toISODate(to) };
}

// Sort tasks soonest-deadline-first. Tasks without a due date sink to the bottom; ties break
// by priority then title so the ordering is stable and meaningful.
export function sortByDeadline(tasks) {
  return [...(tasks || [])].sort((a, b) => {
    const da = parseDueDate(a.due_date);
    const db = parseDueDate(b.due_date);
    if (da && db && da.getTime() !== db.getTime()) return da - db;
    if (da && !db) return -1;
    if (!da && db) return 1;
    const pr = priorityRank(a.priority) - priorityRank(b.priority);
    if (pr !== 0) return pr;
    return (a.title || '').localeCompare(b.title || '');
  });
}

// Pull a structured "Key: value" line out of a task's description blob (the ML pipeline
// writes "Anchor Date", "Raw Duration", "Resolved Date", "Recurrence", "Label", etc.).
// Returns the trimmed, de-quoted value or null when the key is absent.
function descriptionField(description, key) {
  if (!description) return null;
  const re = new RegExp(`^\\s*${key}\\s*:\\s*(.+)$`, 'mi');
  const m = description.match(re);
  if (!m) return null;
  return m[1].trim().replace(/^"(.*)"$/, '$1').trim() || null;
}

// Human-readable date-type label.
export const DATE_TYPE_LABEL = {
  ABSOLUTE: 'Explicit date',
  RELATIVE: 'Computed from signing date',
  RECURRING: 'Recurring schedule',
  CONDITIONAL: 'Event-triggered',
  NONE: 'No deadline',
};

// Explain HOW a task's due date was derived, for the trust-but-verify UI. Reads the
// structured fields the pipeline persists in `description`, falling back gracefully when
// they're absent. Returns a plain object the components render.
export function describeDateDerivation(task) {
  const dateType = (task && task.date_type ? String(task.date_type) : 'NONE').toUpperCase();
  const description = (task && task.description) || '';
  const anchorRaw = descriptionField(description, 'Anchor Date');
  const rawDuration = descriptionField(description, 'Raw Duration');
  const recurrence = descriptionField(description, 'Recurrence');
  const label = descriptionField(description, 'Label');
  const anchorDate = anchorRaw ? formatDueDate(anchorRaw) : null;
  const resolved = task && task.due_date ? formatDueDate(task.due_date) : null;

  let summary;
  switch (dateType) {
    case 'ABSOLUTE':
      summary = 'Stated explicitly in the clause text.';
      break;
    case 'RELATIVE':
      summary = anchorDate
        ? `Anchor (signing) date ${anchorDate}${rawDuration ? ` plus “${rawDuration}”` : ''}.`
        : `Computed from the contract's signing date${rawDuration ? ` plus “${rawDuration}”` : ''}.`;
      break;
    case 'RECURRING':
      summary = recurrence
        ? `Repeats: ${recurrence}. No single due date.`
        : 'Repeating schedule — no single due date.';
      break;
    case 'CONDITIONAL':
      summary = 'Tied to a future event, so no fixed date was assumed (flagged for review).';
      break;
    default:
      summary = 'No deadline language was found in this clause.';
  }

  return {
    dateType,
    typeLabel: DATE_TYPE_LABEL[dateType] || dateType,
    summary,
    anchorDate,
    rawDuration,
    recurrence,
    label,
    resolved,
  };
}

// Group tasks by their due_date (YYYY-MM-DD key, or 'unscheduled'), returning sorted entries.
export function groupByDueDate(tasks) {
  const groups = new Map();
  for (const task of tasks || []) {
    const d = parseDueDate(task.due_date);
    const key = d ? toISODate(d) : 'unscheduled';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(task);
  }
  const entries = [...groups.entries()];
  entries.sort(([a], [b]) => {
    if (a === 'unscheduled') return 1;
    if (b === 'unscheduled') return -1;
    return a < b ? -1 : a > b ? 1 : 0;
  });
  for (const [, list] of entries) {
    list.sort((x, y) => priorityRank(x.priority) - priorityRank(y.priority));
  }
  return entries;
}

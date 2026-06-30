import { forwardRef } from 'react';
import GroundedSpanText from './GroundedSpanText';
import DateDerivation from './DateDerivation';

// A single obligation task, rendered with its grounded source span (Requirements 5.5, 5.6).
//
// Obligation type is distinguished by both a colored left border and a badge:
//   - PROHIBITION -> rose
//   - OBLIGATION  -> emerald
//   - anything else (e.g. RIGHT/PERMISSION or null) -> neutral slate
//
// `requires_review` tasks get a prominent dashed-amber treatment so the eye is drawn to them,
// reinforced on the source-text region itself.
//
// Interaction (coordinated by ContractAnalysis):
//   - Clicking the card scrolls to + flashes its source span (`isFlashing` + `flashNonce`).
//   - Clicking a highlighted span "opens" (activates) this task (`onSpanActivate`).
//   - The active task is visually emphasized (`isActive`) and marked `aria-current`.

function obligationStyles(obligationType) {
  const type = (obligationType || '').toUpperCase();
  if (type === 'PROHIBITION') {
    return {
      border: 'border-l-2 border-l-red-500',
      badge: 'border-red-200 bg-red-50 text-risk-critical',
      label: 'Prohibition',
    };
  }
  if (type === 'OBLIGATION') {
    return {
      border: 'border-l-2 border-l-emerald-500',
      badge: 'border-emerald-200 bg-emerald-50 text-emerald-700',
      label: 'Obligation',
    };
  }
  return {
    border: 'border-l-2 border-l-ink-300',
    badge: 'border-ink-200 bg-ink-50 text-ink-600',
    label: obligationType || 'Other',
  };
}

function ConfidenceMeter({ confidence }) {
  if (typeof confidence !== 'number') return null;
  const pct = Math.max(0, Math.min(100, Math.round(confidence * 100)));
  const tone = pct >= 75 ? 'bg-span-action' : pct >= 50 ? 'bg-risk-medium' : 'bg-risk-critical';
  return (
    <div className="flex items-center gap-1.5" title={`Model confidence: ${pct}%`}>
      <span className="text-2xs font-medium uppercase tracking-wide text-ink-400">Confidence</span>
      <span className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-200">
        <span className={`block h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="tabular text-2xs font-semibold text-ink-600">{pct}%</span>
    </div>
  );
}

/**
 * @param {{
 *   task: object,
 *   isActive?: boolean,
 *   isFlashing?: boolean,
 *   flashNonce?: number,
 *   onActivate?: (taskId: string|number) => void,
 *   onSpanActivate?: (taskId: string|number) => void,
 * }} props
 */
const TaskCard = forwardRef(function TaskCard(
  { task, isActive = false, isFlashing = false, flashNonce = 0, onActivate, onSpanActivate },
  ref,
) {
  const styles = obligationStyles(task.obligation_type);
  const requiresReview = Boolean(task.requires_review);
  const interactive = typeof onActivate === 'function';

  const reviewClasses = requiresReview
    ? 'border border-amber-300 bg-amber-50/40'
    : 'border border-ink-200 bg-white';

  const activeClasses = isActive
    ? 'ring-2 ring-offset-1 ring-brand-500 shadow-card-hover'
    : 'shadow-card';

  const interactiveClasses = interactive
    ? 'cursor-pointer transition-colors hover:border-ink-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1'
    : '';

  const handleActivate = () => {
    if (interactive) onActivate(task.id);
  };

  const handleKeyDown = (event) => {
    if (!interactive) return;
    if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
      event.preventDefault();
      onActivate(task.id);
    }
  };

  const sourceFlashClass = isFlashing ? 'span-flash' : '';
  const sourceReviewClass = requiresReview
    ? 'ring-1 ring-inset ring-amber-200 bg-amber-50/50'
    : 'bg-ink-50 ring-1 ring-inset ring-ink-200/70';

  const hasDateInfo = Boolean(task.due_date) || (task.date_type && task.date_type !== 'NONE');

  return (
    <article
      ref={ref}
      className={`rounded-xl p-4 ${styles.border} ${reviewClasses} ${activeClasses} ${interactiveClasses}`}
      aria-label={task.title || 'Task'}
      aria-current={isActive ? 'true' : undefined}
      onClick={interactive ? handleActivate : undefined}
      onKeyDown={interactive ? handleKeyDown : undefined}
      tabIndex={interactive ? 0 : undefined}
      role={interactive ? 'button' : undefined}
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <h4 className="text-sm font-semibold leading-snug text-ink-900">
          {task.title || 'Untitled obligation'}
        </h4>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          <span
            className={`inline-flex items-center rounded border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide ${styles.badge}`}
          >
            {styles.label}
          </span>
          {requiresReview && (
            <span className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-risk-high">
              Needs review
            </span>
          )}
        </div>
      </header>

      {/* Party + confidence */}
      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-2">
        {task.obligated_party ? (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-span-party/10 px-2 py-0.5 text-xs font-medium text-span-party ring-1 ring-inset ring-span-party/20">
            <span className="h-1.5 w-1.5 rounded-full bg-span-party" aria-hidden="true" />
            {task.obligated_party}
          </span>
        ) : null}
        <ConfidenceMeter confidence={task.confidence} />
      </div>

      {task.action ? (
        <p className="mt-2 text-sm leading-relaxed text-ink-600">
          <span className="font-medium text-ink-400">Action: </span>
          <span className="font-medium text-span-action">{task.action}</span>
        </p>
      ) : null}

      {/* Grounded source clause */}
      <div className="mt-3">
        <span className="section-label mb-1 block">Source clause</span>
        <div
          key={isFlashing ? `flash-${flashNonce}` : 'idle'}
          className={`rounded-lg p-3 ${sourceFlashClass} ${sourceReviewClass}`}
        >
          <GroundedSpanText
            sourceText={task.source_text}
            partyStart={task.agent_start}
            partyEnd={task.agent_end}
            actionStart={task.action_start}
            actionEnd={task.action_end}
            deadlineStart={task.deadline_start}
            deadlineEnd={task.deadline_end}
            onSpanActivate={
              typeof onSpanActivate === 'function' ? () => onSpanActivate(task.id) : undefined
            }
          />
        </div>
      </div>

      {hasDateInfo ? <DateDerivation task={task} className="mt-3" /> : null}
    </article>
  );
});

export default TaskCard;

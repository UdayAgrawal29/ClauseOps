import { Link } from 'react-router-dom';
import { useMemo } from 'react';
import { useDashboardSummary, useContracts } from '../hooks/useTaskQueries';
import { LoadingState, ErrorState, EmptyState } from '../components/StateBlock';
import TaskRow from '../components/TaskRow';
import {
  PRIORITIES,
  STATUSES,
  sortByDeadline,
} from '../lib/taskFormat';

// Severity/status indicator dots — a single small color cue keeps the breakdown calm and
// scannable instead of a tinted "rainbow grid".
const PRIORITY_DOT = {
  CRITICAL: 'bg-risk-critical',
  HIGH: 'bg-risk-high',
  MEDIUM: 'bg-risk-medium',
  LOW: 'bg-ink-300',
};
const STATUS_DOT = {
  PENDING: 'bg-brand-500',
  DONE: 'bg-emerald-500',
  SNOOZED: 'bg-ink-300',
  DISMISSED: 'bg-ink-200',
};

// A neutral stat cell with a small severity dot and a tabular count.
function CountChip({ label, count, dotClass }) {
  return (
    <div className="flex items-center justify-between rounded-md border border-ink-200 bg-white px-3 py-2.5">
      <span className="flex items-center gap-2 text-2xs font-semibold uppercase tracking-wide text-ink-600">
        <span className={`h-1.5 w-1.5 rounded-full ${dotClass || 'bg-ink-300'}`} aria-hidden="true" />
        {label}
      </span>
      <span className="tabular text-sm font-semibold text-ink-900">{count}</span>
    </div>
  );
}

/**
 * Deadline-first home (Requirement 6.2). Surfaces counts by priority and status, a prominent
 * "needs review" indicator linking to the review queue, and the upcoming-deadlines list
 * (soonest first).
 */
export default function Dashboard() {
  const { data, isLoading, isError, error, refetch } = useDashboardSummary();
  const { data: contractsData } = useContracts();

  const contracts = Array.isArray(contractsData) ? contractsData : contractsData?.items || [];
  const contractNameById = useMemo(() => {
    const m = new Map();
    for (const c of contracts) m.set(c.id, c.filename || `Contract #${c.id}`);
    return m;
  }, [contracts]);

  const countsByPriority = data?.counts_by_priority || {};
  const countsByStatus = data?.counts_by_status || {};
  const reviewCount = data?.requires_review_count || 0;
  const upcoming = sortByDeadline(data?.upcoming_deadlines || []);

  return (
    <div className="space-y-5">
      <div className="flex justify-end">
        <Link to="/tasks" className="text-sm font-medium text-brand-700 hover:text-brand-800">
          View all tasks →
        </Link>
      </div>

      {isLoading ? <LoadingState label="Loading your dashboard…" /> : null}
      {isError ? <ErrorState error={error} onRetry={refetch} /> : null}

      {!isLoading && !isError ? (
        <>
          {/* Needs-review indicator linking to the review queue — restrained, not alarmist. */}
          <Link
            to="/review"
            className={`flex items-center justify-between rounded-lg border p-4 transition-colors ${
              reviewCount > 0
                ? 'border-ink-200 border-l-2 border-l-amber-400 bg-white hover:bg-amber-50/40'
                : 'border-ink-200 bg-white hover:border-ink-300'
            }`}
          >
            <div className="flex items-center gap-3">
              <span
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-md border text-sm font-semibold tabular ${
                  reviewCount > 0
                    ? 'border-amber-200 bg-amber-50 text-risk-high'
                    : 'border-ink-200 bg-ink-50 text-ink-500'
                }`}
              >
                {reviewCount}
              </span>
              <div>
                <p className="text-sm font-semibold text-ink-900">
                  {reviewCount > 0
                    ? `${reviewCount} task${reviewCount === 1 ? '' : 's'} need review`
                    : 'Nothing needs review'}
                </p>
                <p className="mt-0.5 text-xs text-ink-500">
                  {reviewCount > 0
                    ? 'Verify or correct uncertain extractions.'
                    : 'All extractions look confident.'}
                </p>
              </div>
            </div>
            <span className="shrink-0 text-sm font-medium text-brand-700">Open review queue →</span>
          </Link>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            <section className="card p-4">
              <h2 className="section-label">By priority</h2>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {PRIORITIES.map((p) => (
                  <CountChip
                    key={p}
                    label={p}
                    count={countsByPriority[p] || 0}
                    dotClass={PRIORITY_DOT[p]}
                  />
                ))}
              </div>
            </section>

            <section className="card p-4">
              <h2 className="section-label">By status</h2>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {STATUSES.map((s) => (
                  <CountChip
                    key={s}
                    label={s}
                    count={countsByStatus[s] || 0}
                    dotClass={STATUS_DOT[s]}
                  />
                ))}
              </div>
            </section>
          </div>

          <section>
            <div className="mb-3 flex items-baseline justify-between">
              <h2 className="text-base font-semibold text-ink-900">Upcoming deadlines</h2>
              <Link
                to="/calendar"
                className="text-sm font-medium text-brand-700 hover:text-brand-800"
              >
                Open calendar →
              </Link>
            </div>
            {upcoming.length === 0 ? (
              <EmptyState
                title="No upcoming deadlines"
                description="Upload a contract to start tracking deadline-bearing obligations."
              />
            ) : (
              <div className="space-y-3">
                {upcoming.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    contractName={contractNameById.get(task.contract_id)}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}

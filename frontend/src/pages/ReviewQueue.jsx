import { useMemo } from 'react';
import { useReviewQueue, useContracts } from '../hooks/useTaskQueries';
import { LoadingState, ErrorState, EmptyState } from '../components/StateBlock';
import TaskRow from '../components/TaskRow';
import { sortByDeadline } from '../lib/taskFormat';

/**
 * Review queue (Requirement 8.3): the owner's tasks flagged requires_review, rendered with
 * the amber / dashed emphasis. Each task shows its source contract and links to that
 * contract's analysis.
 */
export default function ReviewQueue() {
  const { data, isLoading, isError, error, refetch } = useReviewQueue();
  const { data: contractsData } = useContracts();

  const contracts = Array.isArray(contractsData) ? contractsData : contractsData?.items || [];
  const contractNameById = useMemo(() => {
    const m = new Map();
    for (const c of contracts) m.set(c.id, c.filename || `Contract #${c.id}`);
    return m;
  }, [contracts]);

  const tasks = sortByDeadline(Array.isArray(data) ? data : data?.items || []);

  return (
    <div className="space-y-5">
      {isLoading ? <LoadingState label="Loading review queue…" /> : null}
      {isError ? <ErrorState error={error} onRetry={refetch} /> : null}

      {!isLoading && !isError ? (
        tasks.length === 0 ? (
          <EmptyState
            title="Nothing needs review"
            description="All extractions look confident right now."
          />
        ) : (
          <div className="space-y-3">
            <div className="rounded-lg border border-ink-200 border-l-2 border-l-amber-400 bg-white p-3.5">
              <p className="text-sm font-semibold text-ink-900">
                {tasks.length} task{tasks.length === 1 ? '' : 's'} flagged for review
              </p>
              <p className="mt-0.5 text-xs text-ink-500">
                These obligations were extracted with lower confidence or an inferred party.
                Verify each against its source clause and correct any field as needed.
              </p>
            </div>
            {tasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                contractName={contractNameById.get(task.contract_id)}
              />
            ))}
          </div>
        )
      ) : null}
    </div>
  );
}

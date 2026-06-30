import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTasks, useContracts } from '../hooks/useTaskQueries';
import { LoadingState, ErrorState, EmptyState } from '../components/StateBlock';
import TaskFilters from '../components/TaskFilters';
import TaskRow from '../components/TaskRow';
import { sortByDeadline } from '../lib/taskFormat';

const EMPTY_FILTERS = {
  priority: '',
  status: '',
  requires_review: '',
  contract_id: '',
  due_after: '',
  due_before: '',
};

/**
 * Deadline-first, filterable task list (Requirement 6.1).
 *
 * Tasks are GROUPED BY THE CONTRACT (PDF) they came from, so when several contracts are
 * uploaded the user can tell which obligations belong to which document. A "Group by
 * contract" toggle switches to a single flat, deadline-first list. The contract filter is a
 * name dropdown (not a raw id).
 */
export default function Tasks() {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [grouped, setGrouped] = useState(true);

  const activeFilters = useMemo(() => {
    const out = {};
    for (const [k, v] of Object.entries(filters)) {
      if (v !== '' && v != null) out[k] = v;
    }
    return out;
  }, [filters]);

  const { data, isLoading, isError, error, refetch, isFetching } = useTasks(activeFilters);
  const { data: contractsData } = useContracts();

  const contracts = Array.isArray(contractsData) ? contractsData : contractsData?.items || [];
  // contract_id -> filename, for labelling tasks with their source document.
  const contractNameById = useMemo(() => {
    const m = new Map();
    for (const c of contracts) m.set(c.id, c.filename || `Contract #${c.id}`);
    return m;
  }, [contracts]);

  const tasks = sortByDeadline(Array.isArray(data) ? data : data?.items || []);

  // Group tasks by their source contract, each group sorted deadline-first. Groups are
  // ordered by their soonest deadline so the most urgent document floats to the top.
  const groups = useMemo(() => {
    const byContract = new Map();
    for (const t of tasks) {
      const key = t.contract_id ?? '—';
      if (!byContract.has(key)) byContract.set(key, []);
      byContract.get(key).push(t);
    }
    const arr = [...byContract.entries()].map(([contractId, items]) => ({
      contractId,
      name: contractNameById.get(contractId) || `Contract #${contractId}`,
      tasks: sortByDeadline(items),
    }));
    arr.sort((a, b) => {
      const ad = a.tasks.find((t) => t.due_date)?.due_date || '9999';
      const bd = b.tasks.find((t) => t.due_date)?.due_date || '9999';
      return ad.localeCompare(bd);
    });
    return arr;
  }, [tasks, contractNameById]);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-end">
        <label className="flex items-center gap-2 text-sm text-ink-600">
          <input
            type="checkbox"
            checked={grouped}
            onChange={(e) => setGrouped(e.target.checked)}
            className="h-4 w-4 rounded border-ink-300 text-brand-600 focus:ring-brand-500"
          />
          Group by contract
        </label>
      </div>

      <TaskFilters
        filters={filters}
        onChange={setFilters}
        onReset={() => setFilters(EMPTY_FILTERS)}
        contracts={contracts}
      />

      {isLoading ? <LoadingState label="Loading tasks…" /> : null}
      {isError ? <ErrorState error={error} onRetry={refetch} /> : null}

      {!isLoading && !isError ? (
        tasks.length === 0 ? (
          <EmptyState
            title="No tasks match these filters"
            description="Try clearing or widening your filters, or upload a contract."
          />
        ) : (
          <div aria-busy={isFetching} className="space-y-6">
            <p className="text-xs text-ink-500">
              {tasks.length} task{tasks.length === 1 ? '' : 's'}
              {grouped ? ` across ${groups.length} contract${groups.length === 1 ? '' : 's'}` : ''}
            </p>

            {grouped ? (
              groups.map((g) => (
                <section key={g.contractId} className="space-y-3">
                  <div className="flex items-center justify-between gap-3 border-b border-ink-200 pb-2">
                    <h2 className="flex min-w-0 items-center gap-2 text-sm font-semibold text-ink-800">
                      <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0 text-ink-400" aria-hidden="true">
                        <path d="M14 3v4a1 1 0 0 0 1 1h4M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                      <span className="truncate" title={g.name}>{g.name}</span>
                      <span className="tabular shrink-0 rounded border border-ink-200 bg-ink-50 px-1.5 py-0.5 text-2xs font-semibold text-ink-500">
                        {g.tasks.length}
                      </span>
                    </h2>
                    {g.contractId !== '—' ? (
                      <Link
                        to={`/contracts/${g.contractId}`}
                        className="shrink-0 text-xs font-medium text-brand-700 hover:text-brand-800"
                      >
                        Open analysis →
                      </Link>
                    ) : null}
                  </div>
                  <div className="space-y-3">
                    {g.tasks.map((task) => (
                      <TaskRow key={task.id} task={task} />
                    ))}
                  </div>
                </section>
              ))
            ) : (
              <div className="space-y-3">
                {tasks.map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    contractName={contractNameById.get(task.contract_id)}
                  />
                ))}
              </div>
            )}
          </div>
        )
      ) : null}
    </div>
  );
}

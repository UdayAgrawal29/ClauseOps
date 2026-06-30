import { useMemo, useState } from 'react';
import { useCalendar, useContracts } from '../hooks/useTaskQueries';
import { LoadingState, ErrorState, EmptyState } from '../components/StateBlock';
import TaskRow from '../components/TaskRow';
import { currentMonthRange, formatDueDate, groupByDueDate } from '../lib/taskFormat';

const fieldClass = 'field';

/**
 * Calendar view over a date window (Requirement 6.3). Defaults to the current month and lets
 * the owner pick a from/to window, then groups the returned tasks by due_date. Each task is
 * labelled with its source contract.
 */
export default function Calendar() {
  const [range, setRange] = useState(() => currentMonthRange());

  const { data, isLoading, isError, error, refetch, isFetching } = useCalendar(range);
  const { data: contractsData } = useContracts();

  const contracts = Array.isArray(contractsData) ? contractsData : contractsData?.items || [];
  const contractNameById = useMemo(() => {
    const m = new Map();
    for (const c of contracts) m.set(c.id, c.filename || `Contract #${c.id}`);
    return m;
  }, [contracts]);

  const tasks = Array.isArray(data) ? data : data?.items || [];
  const groups = groupByDueDate(tasks);

  const update = (key, value) => setRange((prev) => ({ ...prev, [key]: value }));
  const invalidRange = range.from && range.to && range.from > range.to;

  return (
    <div className="space-y-5">
      <section aria-label="Date window" className="card flex flex-wrap items-end gap-4 p-4">
        <div className="space-y-1">
          <label htmlFor="cal-from" className="field-label">
            From
          </label>
          <input
            id="cal-from"
            type="date"
            className={fieldClass}
            value={range.from}
            onChange={(e) => update('from', e.target.value)}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="cal-to" className="field-label">
            To
          </label>
          <input
            id="cal-to"
            type="date"
            className={fieldClass}
            value={range.to}
            onChange={(e) => update('to', e.target.value)}
          />
        </div>
        <button type="button" onClick={() => setRange(currentMonthRange())} className="btn-secondary">
          This month
        </button>
      </section>

      {invalidRange ? (
        <ErrorState error={{ message: 'The "from" date must be on or before the "to" date.' }} />
      ) : null}

      {!invalidRange && isLoading ? <LoadingState label="Loading calendar…" /> : null}
      {!invalidRange && isError ? <ErrorState error={error} onRetry={refetch} /> : null}

      {!invalidRange && !isLoading && !isError ? (
        groups.length === 0 ? (
          <EmptyState
            title="No deadlines in this window"
            description="Adjust the date range to see more."
          />
        ) : (
          <div className="space-y-6" aria-busy={isFetching}>
            {groups.map(([key, dayTasks]) => (
              <section key={key}>
                <h2 className="mb-2 flex items-baseline gap-2 border-b border-ink-200 pb-1.5 text-sm font-semibold text-ink-800">
                  {key === 'unscheduled' ? 'Unscheduled' : formatDueDate(key)}
                  <span className="tabular font-normal text-ink-400">({dayTasks.length})</span>
                </h2>
                <div className="space-y-3">
                  {dayTasks.map((task) => (
                    <TaskRow
                      key={task.id}
                      task={task}
                      contractName={contractNameById.get(task.contract_id)}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        )
      ) : null}
    </div>
  );
}

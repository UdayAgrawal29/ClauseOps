import { PRIORITIES, STATUSES } from '../lib/taskFormat';

const fieldClass = 'field';
const labelClass = 'field-label';

/**
 * Filter controls for the task list (Requirement 6.1): priority, status, requires_review,
 * contract_id, and due_before / due_after date inputs. Controlled by the parent, which owns
 * the filter state and passes it to the tasks query.
 *
 * @param {{
 *   filters: Record<string, string>,
 *   onChange: (next: Record<string, string>) => void,
 *   onReset: () => void,
 *   contracts?: Array<{id:number, filename:string}>,
 * }} props
 */
export default function TaskFilters({ filters, onChange, onReset, contracts = [] }) {
  const update = (key, value) => onChange({ ...filters, [key]: value });

  const hasActive = Object.values(filters).some((v) => v !== '' && v != null);

  return (
    <section aria-label="Task filters" className="card p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="section-label">Filters</span>
        <button
          type="button"
          onClick={onReset}
          disabled={!hasActive}
          className="text-xs font-medium text-ink-500 transition-colors hover:text-ink-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Clear all
        </button>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="space-y-1">
          <label htmlFor="filter-priority" className={labelClass}>
            Priority
          </label>
          <select
            id="filter-priority"
            className={fieldClass}
            value={filters.priority || ''}
            onChange={(e) => update('priority', e.target.value)}
          >
            <option value="">All priorities</option>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-status" className={labelClass}>
            Status
          </label>
          <select
            id="filter-status"
            className={fieldClass}
            value={filters.status || ''}
            onChange={(e) => update('status', e.target.value)}
          >
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-review" className={labelClass}>
            Review
          </label>
          <select
            id="filter-review"
            className={fieldClass}
            value={filters.requires_review || ''}
            onChange={(e) => update('requires_review', e.target.value)}
          >
            <option value="">All tasks</option>
            <option value="true">Needs review</option>
            <option value="false">Reviewed / not flagged</option>
          </select>
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-contract" className={labelClass}>
            Contract
          </label>
          <select
            id="filter-contract"
            className={fieldClass}
            value={filters.contract_id || ''}
            onChange={(e) => update('contract_id', e.target.value)}
          >
            <option value="">All contracts</option>
            {contracts.map((c) => (
              <option key={c.id} value={c.id}>
                {c.filename || `Contract #${c.id}`}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-due-after" className={labelClass}>
            Due after
          </label>
          <input
            id="filter-due-after"
            type="date"
            className={fieldClass}
            value={filters.due_after || ''}
            onChange={(e) => update('due_after', e.target.value)}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-due-before" className={labelClass}>
            Due before
          </label>
          <input
            id="filter-due-before"
            type="date"
            className={fieldClass}
            value={filters.due_before || ''}
            onChange={(e) => update('due_before', e.target.value)}
          />
        </div>
      </div>
    </section>
  );
}

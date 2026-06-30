import { Link } from 'react-router-dom';
import { useContracts, useDeleteContract } from '../hooks/useTaskQueries';
import { LoadingState, ErrorState, EmptyState } from '../components/StateBlock';
import Icon from '../components/Icon.jsx';

// Map a contract status to a squared status badge.
const STATUS_STYLES = {
  PENDING: 'border-ink-200 bg-ink-50 text-ink-600',
  PROCESSING: 'border-brand-200 bg-brand-50 text-brand-700',
  COMPLETE: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  FAILED: 'border-red-200 bg-red-50 text-risk-critical',
};

function StatusBadge({ status }) {
  const cls = STATUS_STYLES[status] || 'border-ink-200 bg-ink-50 text-ink-600';
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide ${cls}`}
    >
      {status}
    </span>
  );
}

function formatDate(value) {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString();
}

/**
 * Contracts & Analysis hub (a top-level section, like Upload / Tasks / Calendar).
 *
 * Lists every contract the user has uploaded with its processing status. Completed contracts
 * link to their full grounded analysis page; pending/processing ones show their state so the
 * user can see what's still being worked on. This is the direct entry point to analyses —
 * no need to go through a task first.
 */
export default function Contracts() {
  const { data, isLoading, isError, error, refetch } = useContracts();
  const deleteContract = useDeleteContract();
  const contracts = Array.isArray(data) ? data : data?.items || [];

  const handleDelete = (e, c) => {
    // Don't let the click bubble to the row's Link (which would navigate).
    e.preventDefault();
    e.stopPropagation();
    const ok = window.confirm(
      `Delete "${c.filename || `Contract #${c.id}`}"?\n\n` +
        'This permanently removes the contract, all its extracted clauses and tasks, ' +
        'and the stored PDF. This cannot be undone.',
    );
    if (ok) deleteContract.mutate(c.id);
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-ink-500">
          Open a completed contract to see its grounded analysis.
        </p>
        <Link to="/upload" className="btn-primary">
          <Icon name="plus" className="h-4 w-4" />
          Upload a contract
        </Link>
      </div>

      {isLoading ? <LoadingState label="Loading contracts…" /> : null}
      {isError ? <ErrorState error={error} onRetry={refetch} /> : null}

      {!isLoading && !isError ? (
        contracts.length === 0 ? (
          <EmptyState
            title="No contracts yet"
            description="Upload a contract PDF to see its analysis here."
          />
        ) : (
          <ul className="space-y-2.5">
            {contracts.map((c) => {
              const isComplete = c.status === 'COMPLETE';
              const inner = (
                <div className="flex items-center justify-between gap-3 rounded-lg border border-ink-200 bg-white p-4 shadow-card transition-colors hover:border-ink-300">
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-ink-200 bg-ink-50 text-ink-500">
                      <Icon name="document" className="h-5 w-5" />
                    </span>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-ink-900">
                        {c.filename || `Contract #${c.id}`}
                      </p>
                      <p className="mt-0.5 text-xs text-ink-500">
                        {c.contract_type ? `${c.contract_type} · ` : ''}
                        Uploaded {formatDate(c.created_at)}
                        {c.status === 'PROCESSING' ? ` · ${c.progress_pct ?? 0}%` : ''}
                        {c.status === 'FAILED' && c.error_message ? ` · ${c.error_message}` : ''}
                      </p>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <StatusBadge status={c.status} />
                    <button
                      type="button"
                      onClick={(e) => handleDelete(e, c)}
                      disabled={deleteContract.isPending}
                      className="rounded-md border border-ink-200 px-2 py-1 text-xs font-medium text-ink-600 transition-colors hover:border-red-200 hover:bg-red-50 hover:text-risk-critical disabled:opacity-50"
                      aria-label={`Delete ${c.filename || `contract ${c.id}`}`}
                    >
                      Delete
                    </button>
                    {isComplete ? (
                      <span aria-hidden="true" className="text-ink-400">→</span>
                    ) : null}
                  </div>
                </div>
              );
              return (
                <li key={c.id}>
                  {isComplete ? (
                    <Link to={`/contracts/${c.id}`} className="block">
                      {inner}
                    </Link>
                  ) : (
                    inner
                  )}
                </li>
              );
            })}
          </ul>
        )
      ) : null}
    </div>
  );
}

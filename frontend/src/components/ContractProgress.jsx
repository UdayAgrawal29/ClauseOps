import { Link } from 'react-router-dom';
import { useContractProgress } from '../hooks/useContractProgress';

// Human-friendly labels for the pipeline stages (design §Async Processing Workflow).
const STAGE_LABELS = {
  extract: 'Extracting text',
  segment: 'Segmenting clauses',
  classify: 'Classifying clauses',
  ner: 'Identifying entities',
  obligations: 'Detecting obligations',
  normalize_dates: 'Normalizing dates',
  generate_tasks: 'Generating tasks',
};

function stageLabel(stage) {
  if (!stage) return null;
  return STAGE_LABELS[stage] || stage;
}

/**
 * Render live analysis progress for a contract. Prefers the progress WebSocket and falls
 * back to status polling under the hood (see useContractProgress).
 *
 * @param {{ contractId: string|number, filename?: string }} props
 */
export default function ContractProgress({ contractId, filename }) {
  const { stage, progressPct, status, source, isComplete, isFailed, error } =
    useContractProgress(contractId);

  const pct = Math.max(0, Math.min(100, Math.round(progressPct)));
  const label = stageLabel(stage);

  let barColor = 'bg-brand-500';
  if (isComplete) barColor = 'bg-emerald-500';
  if (isFailed) barColor = 'bg-risk-critical';

  const sourceLabel =
    source === 'polling' ? 'Polling' : source === 'socket' ? 'Live' : 'Connecting';

  return (
    <section className="card p-6" aria-live="polite">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-lg font-semibold text-ink-900">
          Analyzing{' '}
          {filename ? <span className="font-normal text-ink-500">{filename}</span> : 'your contract'}
        </h2>
        <span className="inline-flex items-center gap-1.5 rounded border border-ink-200 bg-ink-50 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-ink-500">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              source === 'socket' ? 'bg-emerald-500' : source === 'polling' ? 'bg-brand-500' : 'bg-ink-300'
            }`}
            aria-hidden="true"
          />
          {sourceLabel}
        </span>
      </div>

      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between text-sm text-ink-600">
          <span>
            {isFailed
              ? 'Processing failed'
              : isComplete
                ? 'Analysis complete'
                : label || 'Starting…'}
          </span>
          <span className="tabular text-ink-500">{pct}%</span>
        </div>
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-ink-100"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="Contract analysis progress"
        >
          <div
            className={`h-full rounded-full transition-all duration-500 ${barColor}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {status && !isComplete && !isFailed && (
        <p className="mt-3 text-xs text-ink-400">
          Status: <span className="font-medium text-ink-500">{status}</span>
        </p>
      )}

      {isComplete && (
        <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 p-4">
          <p className="text-sm font-medium text-emerald-800">Your contract has been analyzed.</p>
          <Link
            to={`/contracts/${contractId}`}
            className="mt-2 inline-flex text-sm font-semibold text-emerald-700 underline underline-offset-2 hover:text-emerald-900"
          >
            View the analysis →
          </Link>
        </div>
      )}

      {isFailed && (
        <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-4">
          <p className="text-sm font-medium text-risk-critical">
            We couldn&apos;t finish processing this contract.
          </p>
          <p className="mt-1 text-sm text-red-700">Please delete it and try uploading again.</p>
        </div>
      )}

      {error && !isComplete && !isFailed && (
        <p className="mt-3 text-xs text-risk-critical">
          Lost the live connection and couldn&apos;t reach the status endpoint. Retrying…
        </p>
      )}
    </section>
  );
}

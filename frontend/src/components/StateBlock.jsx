// Small, reusable loading / error / empty states so every deadline-first screen handles
// async states consistently and accessibly.

export function LoadingState({ label = 'Loading…' }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex items-center justify-center gap-3 rounded-lg border border-ink-200 bg-white p-8 text-sm text-ink-500 shadow-card"
    >
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-ink-300 border-t-brand-600"
        aria-hidden="true"
      />
      {label}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  const message =
    (error && (error.message || (typeof error === 'string' ? error : null))) ||
    'Something went wrong while loading. Please try again.';
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-risk-critical shadow-card"
    >
      <p className="font-semibold">We couldn&apos;t load this content.</p>
      <p className="mt-1 text-red-700">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 inline-flex rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-risk-critical transition-colors hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title = 'Nothing here yet', description }) {
  return (
    <div className="rounded-lg border border-dashed border-ink-300 bg-white p-10 text-center shadow-card">
      <p className="text-sm font-semibold text-ink-800">{title}</p>
      {description ? <p className="mt-1 text-sm text-ink-500">{description}</p> : null}
    </div>
  );
}

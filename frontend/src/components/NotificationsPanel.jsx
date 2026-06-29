import { useNotifications, useMarkNotificationRead } from '../hooks/useNotifications';

// Format an ISO timestamp into a short, locale-aware label. Falls back to the raw string if
// it cannot be parsed.
function formatTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

/**
 * Lists the owner's notifications (Requirement 9.4): newest first, unread emphasized, with a
 * per-item "Mark read" action and a "Mark all read" action. Shows an unread-count badge.
 *
 * The backend already returns notifications newest-first; this component does not re-sort, so
 * it stays faithful to server ordering.
 *
 * @param {{ className?: string }} props
 */
export default function NotificationsPanel({ className = '' }) {
  const { notifications, unreadCount, isLoading, isError, error } = useNotifications();
  const markRead = useMarkNotificationRead();

  function handleMarkRead(id) {
    markRead.mutate(id);
  }

  function handleMarkAll() {
    for (const item of notifications) {
      if (!item.read) markRead.mutate(item.id);
    }
  }

  return (
    <section className={`card ${className}`} aria-label="Notifications">
      <header className="flex items-center justify-between gap-4 border-b border-ink-100 px-4 py-3">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-semibold text-ink-900">Notifications</h2>
          {unreadCount > 0 && (
            <span
              className="tabular inline-flex min-w-[1.125rem] items-center justify-center rounded-full bg-brand-600 px-1.5 text-2xs font-semibold text-white"
              aria-label={`${unreadCount} unread`}
            >
              {unreadCount}
            </span>
          )}
        </div>
        {unreadCount > 0 && (
          <button
            type="button"
            onClick={handleMarkAll}
            disabled={markRead.isPending}
            className="text-xs font-medium text-brand-700 hover:text-brand-800 disabled:cursor-not-allowed disabled:text-ink-300"
          >
            Mark all read
          </button>
        )}
      </header>

      <div aria-live="polite">
        {isLoading && <p className="px-4 py-6 text-sm text-ink-500">Loading notifications…</p>}

        {isError && !isLoading && (
          <p role="alert" className="px-4 py-6 text-sm text-risk-critical">
            {error?.message || 'Could not load notifications. Please try again.'}
          </p>
        )}

        {!isLoading && !isError && notifications.length === 0 && (
          <p className="px-4 py-6 text-sm text-ink-500">You&apos;re all caught up.</p>
        )}

        {!isLoading && !isError && notifications.length > 0 && (
          <ul className="divide-y divide-ink-100">
            {notifications.map((item) => (
              <li
                key={item.id}
                className={`flex items-start justify-between gap-3 px-4 py-3 ${
                  item.read ? 'bg-white' : 'bg-brand-50/50'
                }`}
              >
                <div className="min-w-0">
                  <p
                    className={`text-sm ${
                      item.read ? 'font-normal text-ink-600' : 'font-semibold text-ink-900'
                    }`}
                  >
                    {!item.read && (
                      <span
                        className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-brand-500 align-middle"
                        aria-hidden="true"
                      />
                    )}
                    {item.message}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-400">{formatTimestamp(item.created_at)}</p>
                </div>
                {!item.read && (
                  <button
                    type="button"
                    onClick={() => handleMarkRead(item.id)}
                    disabled={markRead.isPending}
                    className="shrink-0 text-xs font-medium text-brand-700 hover:text-brand-800 disabled:cursor-not-allowed disabled:text-ink-300"
                  >
                    Mark read
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

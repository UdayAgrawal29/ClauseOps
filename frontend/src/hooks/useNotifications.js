import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';

export const NOTIFICATIONS_QUERY_KEY = ['notifications'];

// Poll notifications periodically so reminder-generated messages (Requirement 9.2/9.4) show
// up without a manual refresh. The backend returns them newest-first already.
const NOTIFICATIONS_POLL_MS = 60_000;

/**
 * Query hook for `GET /notifications` — the authenticated owner's notifications, newest
 * first (Requirement 9.4). Returns the raw TanStack Query result plus a convenience
 * `unreadCount`.
 *
 * @param {{ enabled?: boolean, refetchInterval?: number | false }} [options]
 */
export function useNotifications(options = {}) {
  const { enabled = true, refetchInterval = NOTIFICATIONS_POLL_MS } = options;

  const query = useQuery({
    queryKey: NOTIFICATIONS_QUERY_KEY,
    queryFn: () => api.get('/notifications'),
    enabled,
    refetchInterval,
  });

  const notifications = Array.isArray(query.data) ? query.data : [];
  const unreadCount = notifications.reduce((n, item) => (item && !item.read ? n + 1 : n), 0);

  return { ...query, notifications, unreadCount };
}

/**
 * Mutation hook to mark a single notification read via `PATCH /notifications/{id}`
 * (Requirement 9.4). Invalidates the notifications query on success so the unread count and
 * list re-render with the authoritative state.
 *
 * The mutate function takes the notification id.
 */
export function useMarkNotificationRead() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (notificationId) =>
      api.patch(`/notifications/${notificationId}`, { read: true }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY });
    },
  });
}

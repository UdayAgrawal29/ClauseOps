import NotificationsPanel from '../components/NotificationsPanel';

/**
 * Standalone notifications page (mounted at `/notifications` by the orchestrator). Renders the
 * reusable NotificationsPanel; the panel handles fetching, unread emphasis, and mark-as-read
 * (Requirement 9.4).
 */
export default function Notifications() {
  return (
    <div className="mx-auto max-w-2xl">
      <NotificationsPanel />
    </div>
  );
}

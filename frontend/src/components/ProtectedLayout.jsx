import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../store/auth';
import AppShell from './AppShell.jsx';

/**
 * Layout-route guard for the authenticated app. Waits for the initial silent-refresh attempt
 * to settle (`initialized`) before deciding, so a returning user is not briefly bounced to
 * /login on reload. Once settled, an unauthenticated user is redirected to /login (preserving
 * the attempted location); otherwise the full app shell (sidebar + top bar + page) renders.
 */
export default function ProtectedLayout() {
  const location = useLocation();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const initialized = useAuthStore((s) => s.initialized);

  if (!initialized) {
    return (
      <div
        className="flex h-screen items-center justify-center bg-ink-100"
        role="status"
        aria-live="polite"
      >
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <AppShell />;
}

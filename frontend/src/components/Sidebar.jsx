import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuthStore } from '../store/auth';
import { useNotifications } from '../hooks/useNotifications';
import Icon from './Icon.jsx';

const NAV = [
  { to: '/', label: 'Dashboard', icon: 'dashboard', end: true },
  { to: '/upload', label: 'Upload', icon: 'upload' },
  { to: '/contracts', label: 'Analysis', icon: 'document' },
  { to: '/tasks', label: 'Tasks', icon: 'tasks' },
  { to: '/calendar', label: 'Calendar', icon: 'calendar' },
  { to: '/review', label: 'Review', icon: 'flag' },
  { to: '/notifications', label: 'Notifications', icon: 'bell' },
];

function linkClass({ isActive }) {
  return [
    'group relative flex items-center gap-3 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
    isActive
      ? 'bg-brand-50 text-brand-700'
      : 'text-ink-600 hover:bg-ink-100 hover:text-ink-900',
  ].join(' ');
}

/**
 * Left navigation sidebar: brand, icon nav with active highlighting, an unread badge on
 * Notifications, and the account / sign-out footer. The professional dashboard shell.
 */
export default function Sidebar() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const logout = useAuthStore((s) => s.logout);
  const [loggingOut, setLoggingOut] = useState(false);
  const { unreadCount } = useNotifications({ enabled: isAuthenticated });

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
      navigate('/login', { replace: true });
    } finally {
      setLoggingOut(false);
    }
  };

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-ink-200 bg-white">
      {/* Brand — solid mark, no gradient. */}
      <div className="flex items-center gap-2.5 border-b border-ink-100 px-4 py-4">
        <span className="flex h-8 w-8 items-center justify-center rounded-md bg-brand-700 text-white">
          <Icon name="shield" className="h-[18px] w-[18px]" />
        </span>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-tight text-ink-900">ClauseOps</p>
          <p className="text-2xs uppercase tracking-[0.08em] text-ink-400">Contract Intelligence</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 px-2.5 py-3">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={linkClass}>
            {({ isActive }) => (
              <>
                {/* Left accent rail marks the active section without a heavy fill. */}
                <span
                  aria-hidden="true"
                  className={`absolute inset-y-1 left-0 w-0.5 rounded-full ${
                    isActive ? 'bg-brand-600' : 'bg-transparent'
                  }`}
                />
                <Icon
                  name={item.icon}
                  className={`h-[18px] w-[18px] ${isActive ? 'text-brand-600' : 'text-ink-400 group-hover:text-ink-600'}`}
                />
                <span className="flex-1">{item.label}</span>
                {item.to === '/notifications' && unreadCount > 0 ? (
                  <span
                    className="inline-flex min-w-[1.125rem] items-center justify-center rounded-full bg-risk-critical px-1.5 text-2xs font-bold text-white"
                    aria-label={`${unreadCount} unread`}
                  >
                    {unreadCount > 99 ? '99+' : unreadCount}
                  </span>
                ) : null}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Account / sign out */}
      <div className="border-t border-ink-200 p-2.5">
        <div className="flex items-center gap-3 rounded-md px-2 py-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold uppercase text-brand-700">
            {(user?.email || '?').charAt(0)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-ink-700" title={user?.email}>
              {user?.email || 'Signed in'}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          disabled={loggingOut}
          className="mt-0.5 flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900 disabled:opacity-60"
        >
          <Icon name="logout" className="h-[18px] w-[18px] text-ink-400" />
          {loggingOut ? 'Signing out…' : 'Sign out'}
        </button>
      </div>
    </aside>
  );
}

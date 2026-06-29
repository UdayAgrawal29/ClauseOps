import { useLocation, Outlet } from 'react-router-dom';
import Sidebar from './Sidebar.jsx';

// Page-title + subtitle shown in the slim top bar, keyed by route.
const TOPBAR = [
  [/^\/$/, { title: 'Dashboard', subtitle: "What's due and what needs your attention" }],
  [/^\/upload$/, { title: 'Upload a contract', subtitle: 'Add a PDF to analyze' }],
  [/^\/contracts\/\d+/, { title: 'Contract analysis', subtitle: 'Grounded obligations and source spans' }],
  [/^\/contracts$/, { title: 'Analysis', subtitle: 'Your uploaded contracts' }],
  [/^\/tasks$/, { title: 'Tasks', subtitle: 'Obligations grouped by contract' }],
  [/^\/calendar$/, { title: 'Calendar', subtitle: 'Deadlines by date' }],
  [/^\/review$/, { title: 'Review queue', subtitle: 'Uncertain extractions to verify' }],
  [/^\/notifications$/, { title: 'Notifications', subtitle: 'Reminders and updates' }],
];

function topbarFor(pathname) {
  const match = TOPBAR.find(([re]) => re.test(pathname));
  return match ? match[1] : { title: 'ClauseOps', subtitle: '' };
}

/**
 * The authenticated app shell: a fixed sidebar, a slim contextual top bar, and the routed
 * page content in a comfortable, centered column.
 */
export default function AppShell() {
  const { pathname } = useLocation();
  const { title, subtitle } = topbarFor(pathname);

  return (
    <div className="flex h-screen overflow-hidden bg-ink-100">
      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Slim contextual top bar — solid, hairline-bordered, no blur/translucency. */}
        <header className="flex h-14 shrink-0 items-center border-b border-ink-200 bg-white px-6">
          <div className="min-w-0">
            <h1 className="truncate text-base font-semibold text-ink-900">{title}</h1>
            {subtitle ? <p className="truncate text-xs text-ink-500">{subtitle}</p> : null}
          </div>
        </header>

        {/* Scrollable content */}
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-5xl animate-fade-in-up px-6 py-7">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

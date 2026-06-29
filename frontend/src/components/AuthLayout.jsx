import { Outlet } from 'react-router-dom';
import Icon from './Icon.jsx';

/**
 * Two-pane auth shell: a branded marketing/trust panel on the left and the form (rendered via
 * <Outlet/>) on the right. On small screens the brand panel collapses to a compact header.
 */
export default function AuthLayout() {
  return (
    <div className="flex min-h-screen bg-ink-100">
      {/* Brand / value panel — solid deep navy, no gradient. */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden bg-brand-900 p-12 text-white lg:flex">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-md bg-white/10 ring-1 ring-inset ring-white/15">
            <Icon name="shield" className="h-5 w-5" />
          </span>
          <span className="text-lg font-semibold tracking-tight">ClauseOps</span>
        </div>

        <div className="max-w-md">
          <h2 className="text-2xl font-semibold leading-snug">
            Turn a contract PDF into a deadline-first task tracker.
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-brand-100">
            Upload a contract and watch it become a prioritized list of obligations — each one
            grounded to the exact words it came from, so you can trust and verify every
            deadline.
          </p>
          <ul className="mt-8 space-y-3 text-sm text-brand-50">
            <li className="flex items-center gap-3">
              <Icon name="calendar" className="h-5 w-5 text-brand-300" />
              Deadline-first dashboard, tasks, and calendar
            </li>
            <li className="flex items-center gap-3">
              <Icon name="document" className="h-5 w-5 text-brand-300" />
              Source-grounded extractions with confidence
            </li>
            <li className="flex items-center gap-3">
              <Icon name="flag" className="h-5 w-5 text-brand-300" />
              Uncertain items flagged for your review
            </li>
          </ul>
        </div>

        <p className="text-xs text-brand-200">
          Private by design — your contracts never leave your machine.
        </p>
      </div>

      {/* Form pane */}
      <div className="flex w-full flex-col items-center justify-center px-6 py-12 lg:w-1/2">
        {/* Compact brand for small screens */}
        <div className="mb-8 flex items-center gap-2.5 lg:hidden">
          <span className="flex h-9 w-9 items-center justify-center rounded-md bg-brand-700 text-white">
            <Icon name="shield" className="h-5 w-5" />
          </span>
          <span className="text-lg font-semibold text-ink-900">ClauseOps</span>
        </div>
        <div className="w-full max-w-sm animate-fade-in-up">
          <Outlet />
        </div>
      </div>
    </div>
  );
}

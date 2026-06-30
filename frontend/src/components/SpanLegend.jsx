// Color legend for the grounded source-span viewer (Requirement 5.5).
// Organised into three clear groups so the user can interpret the highlights, the
// obligation-type badges, and the review treatment at a glance.

const HIGHLIGHTS = [
  {
    key: 'party',
    label: 'Obligated party',
    swatch: 'bg-span-party/15 ring-1 ring-inset ring-span-party/40',
  },
  {
    key: 'action',
    label: 'Action',
    swatch: 'bg-span-action/15 ring-1 ring-inset ring-span-action/40',
  },
  {
    key: 'deadline',
    label: 'Deadline',
    swatch: 'bg-span-deadline/15 ring-1 ring-inset ring-span-deadline/40',
  },
];

const TYPES = [
  { key: 'obligation', label: 'Obligation', pill: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  { key: 'prohibition', label: 'Prohibition', pill: 'border-red-200 bg-red-50 text-risk-critical' },
  { key: 'permission', label: 'Permission', pill: 'border-ink-200 bg-ink-50 text-ink-600' },
];

function GroupLabel({ children }) {
  return <span className="section-label mb-2 block">{children}</span>;
}

export default function SpanLegend() {
  return (
    <div className="card p-4" aria-label="Highlight legend">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-8">
        {/* Source highlights */}
        <div className="min-w-0">
          <GroupLabel>Source highlights</GroupLabel>
          <ul className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-slate-600">
            {HIGHLIGHTS.map((item) => (
              <li key={item.key} className="flex items-center gap-2">
                <span aria-hidden="true" className={`inline-block h-4 w-6 rounded ${item.swatch}`} />
                <span>{item.label}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="hidden w-px self-stretch bg-slate-200 sm:block" aria-hidden="true" />

        {/* Obligation type */}
        <div className="min-w-0">
          <GroupLabel>Obligation type</GroupLabel>
          <ul className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            {TYPES.map((item) => (
              <li key={item.key}>
                <span
                  className={`inline-flex items-center rounded border px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide ${item.pill}`}
                >
                  {item.label}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="hidden w-px self-stretch bg-slate-200 sm:block" aria-hidden="true" />

        {/* Review status */}
        <div className="min-w-0">
          <GroupLabel>Status</GroupLabel>
          <ul className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <li className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="inline-block h-4 w-6 rounded border-2 border-dashed border-review bg-review/10"
              />
              <span>Needs review</span>
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}

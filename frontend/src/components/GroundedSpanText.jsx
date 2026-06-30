// Grounded source-span renderer (design §Grounded Source-Span Viewer, Requirement 5.5).
//
// Given a task's `source_text` and its party/action/deadline character offsets, this
// renders the text with the relevant ranges wrapped in distinct visual marks:
//   - PARTY    -> blue   (theme color `span.party`)
//   - ACTION   -> green  (theme color `span.action`)
//   - DEADLINE -> amber  (theme color `span.deadline`)
//
// The grounding guarantee from the pipeline is `source_text[agent_start:agent_end] ===
// obligated_party` and `source_text[action_start:action_end] === action` (when offsets are
// non-null), so the highlighted characters are exactly the extracted text.
//
// DEADLINE-SPAN DECISION: the backend's ContractDetail task does NOT currently expose a
// persisted deadline offset (only `agent_start/agent_end`, `action_start/action_end`,
// `due_date`, `date_type`). Rather than guess at a date position (which risks highlighting
// the wrong characters and violating the trust-but-verify principle), this component takes a
// clean, optional `deadlineStart`/`deadlineEnd` pair. When the backend later persists a
// deadline raw-text offset, the page passes it through and the highlight appears with zero
// further changes. Until then, party + action are highlighted and the deadline mark is
// simply omitted.

// Span priority when ranges overlap: the earlier-listed kind wins for the overlapping
// slice. Party is the most identity-bearing, so it takes precedence.
const KIND_PRIORITY = ['party', 'action', 'deadline'];

// Tailwind mark classes per kind. Uses the `span.*` theme palette (tailwind.config.js).
const MARK_CLASSES = {
  party:
    'rounded-sm bg-span-party/15 text-span-party ring-1 ring-inset ring-span-party/30 px-0.5',
  action:
    'rounded-sm bg-span-action/15 text-span-action ring-1 ring-inset ring-span-action/30 px-0.5',
  deadline:
    'rounded-sm bg-span-deadline/15 text-span-deadline ring-1 ring-inset ring-span-deadline/30 px-0.5',
};

export const SPAN_KIND_LABELS = {
  party: 'Obligated party',
  action: 'Action',
  deadline: 'Deadline',
};

/**
 * Normalize a raw [start, end] pair against the text length. Returns null for absent,
 * inverted, zero-width, or out-of-bounds ranges so callers can ignore them gracefully.
 *
 * @param {number|null|undefined} start
 * @param {number|null|undefined} end
 * @param {number} textLength
 * @returns {{start:number,end:number}|null}
 */
function normalizeRange(start, end, textLength) {
  if (start === null || start === undefined || end === null || end === undefined) {
    return null;
  }
  if (!Number.isInteger(start) || !Number.isInteger(end)) return null;
  const clampedStart = Math.max(0, Math.min(start, textLength));
  const clampedEnd = Math.max(0, Math.min(end, textLength));
  if (clampedEnd <= clampedStart) return null;
  return { start: clampedStart, end: clampedEnd };
}

/**
 * Build an ordered list of non-overlapping segments covering the whole text. Each segment is
 * `{ text, kind }` where `kind` is one of the span kinds or null (unhighlighted).
 *
 * Overlapping and adjacent spans are handled by splitting the text at every span boundary and
 * assigning each resulting slice the highest-priority kind that covers it. This guarantees the
 * concatenation of all segment texts equals the original text exactly.
 *
 * @param {string} text
 * @param {Array<{start:number,end:number,kind:string}>} spans
 * @returns {Array<{text:string,kind:string|null,start:number,end:number}>}
 */
export function buildSegments(text, spans) {
  const safeText = typeof text === 'string' ? text : '';
  const length = safeText.length;

  const valid = (spans || [])
    .map((s) => {
      const range = normalizeRange(s.start, s.end, length);
      return range ? { ...range, kind: s.kind } : null;
    })
    .filter(Boolean);

  if (valid.length === 0) {
    return length > 0 ? [{ text: safeText, kind: null, start: 0, end: length }] : [];
  }

  // Collect unique sorted boundaries: text bounds plus every span start/end.
  const boundarySet = new Set([0, length]);
  for (const s of valid) {
    boundarySet.add(s.start);
    boundarySet.add(s.end);
  }
  const boundaries = Array.from(boundarySet).sort((a, b) => a - b);

  const segments = [];
  for (let i = 0; i < boundaries.length - 1; i += 1) {
    const segStart = boundaries[i];
    const segEnd = boundaries[i + 1];
    if (segEnd <= segStart) continue;

    // Find every span covering this slice, then pick the highest-priority kind.
    const coveringKinds = valid
      .filter((s) => s.start <= segStart && s.end >= segEnd)
      .map((s) => s.kind);

    let kind = null;
    for (const candidate of KIND_PRIORITY) {
      if (coveringKinds.includes(candidate)) {
        kind = candidate;
        break;
      }
    }

    segments.push({
      text: safeText.slice(segStart, segEnd),
      kind,
      start: segStart,
      end: segEnd,
    });
  }

  return segments;
}

/**
 * Render a task's `source_text` with grounded party/action/deadline highlights.
 *
 * @param {{
 *   sourceText: string,
 *   partyStart?: number|null,
 *   partyEnd?: number|null,
 *   actionStart?: number|null,
 *   actionEnd?: number|null,
 *   deadlineStart?: number|null,
 *   deadlineEnd?: number|null,
 *   className?: string,
 *   onSpanActivate?: (kind: string) => void,
 * }} props
 */
export default function GroundedSpanText({
  sourceText,
  partyStart,
  partyEnd,
  actionStart,
  actionEnd,
  deadlineStart,
  deadlineEnd,
  className = '',
  onSpanActivate,
}) {
  if (!sourceText) {
    return (
      <p className={`text-sm italic text-slate-400 ${className}`}>
        No source text available for this task.
      </p>
    );
  }

  const segments = buildSegments(sourceText, [
    { start: partyStart, end: partyEnd, kind: 'party' },
    { start: actionStart, end: actionEnd, kind: 'action' },
    { start: deadlineStart, end: deadlineEnd, kind: 'deadline' },
  ]);

  // When the page wires up interactions, each highlighted span becomes an accessible control
  // that opens (activates) its associated task. Without a handler the marks stay purely visual.
  const interactive = typeof onSpanActivate === 'function';

  const handleSpanKeyDown = (event, kind) => {
    if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
      event.preventDefault();
      event.stopPropagation();
      onSpanActivate(kind);
    }
  };

  const handleSpanClick = (event, kind) => {
    // Stop the click bubbling to the card so a span click "opens the task" rather than also
    // triggering the card's own flash interaction.
    event.stopPropagation();
    onSpanActivate(kind);
  };

  return (
    <p
      className={`whitespace-pre-wrap break-words [overflow-wrap:anywhere] text-reading leading-relaxed text-ink-800 ${className}`}
    >
      {segments.map((segment) =>
        segment.kind ? (
          <mark
            key={`${segment.start}-${segment.end}`}
            className={`bg-transparent ${MARK_CLASSES[segment.kind]}${
              interactive
                ? ' cursor-pointer transition-shadow hover:ring-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-1'
                : ''
            }`}
            // Expose the span meaning to assistive tech without disrupting reading flow.
            aria-label={
              interactive
                ? `Open task — ${SPAN_KIND_LABELS[segment.kind]}: ${segment.text}`
                : `${SPAN_KIND_LABELS[segment.kind]}: ${segment.text}`
            }
            title={SPAN_KIND_LABELS[segment.kind]}
            role={interactive ? 'button' : undefined}
            tabIndex={interactive ? 0 : undefined}
            onClick={interactive ? (e) => handleSpanClick(e, segment.kind) : undefined}
            onKeyDown={interactive ? (e) => handleSpanKeyDown(e, segment.kind) : undefined}
          >
            {segment.text}
          </mark>
        ) : (
          <span key={`${segment.start}-${segment.end}`}>{segment.text}</span>
        ),
      )}
    </p>
  );
}

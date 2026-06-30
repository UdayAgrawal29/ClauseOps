import { useForm } from 'react-hook-form';
import { ApiError } from '../lib/api';
import { useTaskMutation } from '../hooks/useTaskMutation';

// Priority option set mirrors the values the backend pipeline emits.
const PRIORITY_OPTIONS = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'];

// The fields this form lets an owner correct (Requirement 7.3). Order drives the layout.
//
// NOTE: the technical `date_type` and the raw `description` blob are intentionally NOT
// editable here — they're pipeline internals (editing the date-type *label* doesn't recompute
// the deadline, which is confusing). The meaningful, user-facing fields are exposed instead.
// How a deadline was derived is shown read-only in the "Deadline" box above this form.
const FIELD_DEFS = [
  { name: 'title', label: 'Title', type: 'text', full: true },
  { name: 'obligated_party', label: 'Obligated party', type: 'text' },
  { name: 'beneficiary', label: 'Beneficiary', type: 'text' },
  { name: 'action', label: 'Action', type: 'text', full: true },
  { name: 'due_date', label: 'Due date', type: 'date' },
  { name: 'priority', label: 'Priority', type: 'select', options: PRIORITY_OPTIONS },
];

// Normalize a raw task value into a controlled-input string (never null/undefined).
function toFieldValue(value) {
  if (value == null) return '';
  return String(value);
}

function buildDefaults(task) {
  const defaults = {};
  for (const def of FIELD_DEFS) {
    defaults[def.name] = toFieldValue(task?.[def.name]);
  }
  return defaults;
}

function messageForError(err) {
  if (err instanceof ApiError) {
    if (err.status === 422) return 'Some values were rejected. The task was left unchanged.';
    if (err.status === 401) return 'Your session has expired. Please sign in again.';
    if (err.status === 404) return 'This task could not be found.';
    return err.message || 'Could not save your corrections. Please try again.';
  }
  return 'Something went wrong while saving. Please try again.';
}

const inputClasses = 'field';

/**
 * A form to correct one or more fields of a task via `PATCH /tasks/{id}` (Requirement 7.3).
 * Only fields the user actually changed are sent. On success the task is refetched (via cache
 * invalidation in useTaskMutation), so it reflects the updated values and
 * `is_user_corrected = true`.
 *
 * @param {{
 *   task: object,
 *   contractId?: string|number,
 *   onSaved?: (task: object) => void,
 *   onCancel?: () => void,
 * }} props
 */
export default function TaskFieldCorrection({ task, contractId, onSaved, onCancel }) {
  const taskId = task?.id;
  const resolvedContractId = contractId ?? task?.contract_id;

  const {
    register,
    handleSubmit,
    reset,
    formState: { isDirty, dirtyFields },
  } = useForm({ defaultValues: buildDefaults(task) });

  const mutation = useTaskMutation(taskId, {
    contractId: resolvedContractId,
    onSuccess: (updated) => {
      reset(buildDefaults(updated));
      if (onSaved) onSaved(updated);
    },
  });

  function onSubmit(values) {
    // Build a patch containing only the fields the user edited. Empty string clears a value
    // (sent as null) so users can remove an incorrect extraction.
    const patch = {};
    for (const def of FIELD_DEFS) {
      if (!dirtyFields[def.name]) continue;
      const raw = values[def.name];
      patch[def.name] = raw === '' ? null : raw;
    }
    if (Object.keys(patch).length === 0) return;
    mutation.mutate(patch);
  }

  const error = mutation.isError ? messageForError(mutation.error) : null;
  const formId = `task-correction-${taskId}`;

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      className="panel p-4"
      aria-label="Correct task fields"
    >
      <div className="mb-3 flex items-center gap-2">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-brand-200 bg-brand-50 text-brand-700">
          <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-3.5 w-3.5">
            <path d="M13.586 3.586a2 2 0 1 1 2.828 2.828l-8.5 8.5a2 2 0 0 1-.878.506l-3.07.82a.75.75 0 0 1-.92-.92l.82-3.07a2 2 0 0 1 .506-.878l8.5-8.5Z" />
          </svg>
        </span>
        <div>
          <h5 className="text-sm font-semibold text-ink-900">Correct fields</h5>
          <p className="text-xs text-ink-500">
            Fix anything the model got wrong. Only changed fields are saved.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
        {FIELD_DEFS.map((def) => {
          const fieldId = `${formId}-${def.name}`;
          return (
            <div key={def.name} className={`space-y-1 ${def.full ? 'sm:col-span-2' : ''}`}>
              <label htmlFor={fieldId} className="field-label">
                {def.label}
              </label>
              {def.type === 'select' ? (
                <select id={fieldId} {...register(def.name)} className={inputClasses}>
                  <option value="">— unset —</option>
                  {def.options.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              ) : (
                <input id={fieldId} type={def.type} {...register(def.name)} className={inputClasses} />
              )}
            </div>
          );
        })}
      </div>

      {error ? (
        <p role="alert" aria-live="polite" className="mt-3 text-xs text-risk-critical">
          {error}
        </p>
      ) : null}

      <div className="mt-4 flex items-center gap-3 border-t border-ink-100 pt-3">
        <button type="submit" disabled={mutation.isPending || !isDirty} className="btn-primary">
          {mutation.isPending ? 'Saving…' : 'Save corrections'}
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={mutation.isPending}
            className="text-sm font-medium text-ink-500 transition-colors hover:text-ink-700 disabled:cursor-not-allowed disabled:text-ink-300"
          >
            Cancel
          </button>
        )}
        {!isDirty && !mutation.isPending ? (
          <span className="ml-auto text-xs text-ink-400">Edit a field to enable saving</span>
        ) : null}
        {task?.is_user_corrected && isDirty ? (
          <span className="ml-auto inline-flex items-center rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-2xs font-semibold uppercase tracking-wide text-risk-high">
            Edited
          </span>
        ) : null}
      </div>
    </form>
  );
}

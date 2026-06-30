import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';

// The set of statuses the backend accepts (Requirement 7.1/7.2). Mirrored here so the UI can
// offer the valid options and label them; the server remains the source of truth and rejects
// anything else with a 422 (task left unchanged).
export const TASK_STATUSES = ['PENDING', 'DONE', 'SNOOZED', 'DISMISSED'];

// Fields an owner may correct (Requirement 7.3). Used by the correction form; sending any of
// these in the PATCH body causes the server to record them in `corrected_fields` and set
// `is_user_corrected` to true.
export const CORRECTABLE_FIELDS = [
  'title',
  'description',
  'obligated_party',
  'beneficiary',
  'action',
  'due_date',
  'date_type',
  'priority',
];

/**
 * Query keys that may hold a copy of a task and therefore need to be refreshed after a
 * mutation. We invalidate broadly (rather than surgically patching every cache) so the task
 * list, a single contract's analysis view, and the dashboard summary all reflect the change.
 *
 * @param {string|number|null|undefined} contractId
 * @returns {Array<Array<unknown>>}
 */
export function taskRelatedQueryKeys(contractId) {
  const keys = [['tasks'], ['dashboard-summary'], ['calendar'], ['review-queue']];
  if (contractId != null) {
    // The analysis view caches under ['contract-analysis', id] (see useContractAnalysis).
    keys.push(['contract-analysis', String(contractId)]);
    keys.push(['contract-analysis', Number(contractId)]);
  } else {
    // Without a known contract id, refresh any cached contract analysis view.
    keys.push(['contract-analysis']);
  }
  return keys;
}

/**
 * TanStack Query mutation wrapping `PATCH /tasks/{id}`.
 *
 * Accepts a partial task body (a `status` change and/or one or more correctable fields) and,
 * on success, invalidates the task / contract / dashboard queries so dependent views refetch
 * the authoritative server state (including `is_user_corrected` and `corrected_fields`).
 *
 * @param {string|number} taskId
 * @param {{ contractId?: string|number, onSuccess?: (task: object) => void }} [options]
 */
export function useTaskMutation(taskId, options = {}) {
  const { contractId, onSuccess } = options;
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (patch) => api.patch(`/tasks/${taskId}`, patch),
    onSuccess: (updatedTask) => {
      for (const key of taskRelatedQueryKeys(contractId)) {
        queryClient.invalidateQueries({ queryKey: key });
      }
      if (onSuccess) onSuccess(updatedTask);
    },
  });
}

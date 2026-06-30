import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';

// TanStack Query hooks over the read APIs that back the deadline-first screens
// (Requirements 6.1-6.3, 8.3). All requests flow through `api.get`, which prepends `/api`
// and attaches the Bearer token.

/**
 * Dashboard summary: counts by priority and status, the requires_review count, and the
 * upcoming deadlines list (Requirement 6.2).
 */
export function useDashboardSummary() {
  return useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: () => api.get('/dashboard/summary'),
  });
}

// Drop empty / nullish filter values so we only send meaningful query params.
function buildQueryString(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

/**
 * Owner task list with optional filters: priority, status, due_before, due_after,
 * requires_review, contract_id (+ limit/offset) (Requirement 6.1).
 *
 * @param {Record<string, string|number|boolean|undefined>} [filters]
 */
export function useTasks(filters = {}) {
  const qs = buildQueryString(filters);
  return useQuery({
    queryKey: ['tasks', filters],
    queryFn: () => api.get(`/tasks${qs}`),
    placeholderData: keepPreviousData,
  });
}

/**
 * Calendar tasks whose due_date falls within [from, to] (Requirement 6.3).
 *
 * @param {{ from?: string, to?: string }} range  ISO date strings (YYYY-MM-DD).
 */
export function useCalendar(range = {}) {
  const { from, to } = range;
  const qs = buildQueryString({ from, to });
  return useQuery({
    queryKey: ['calendar', from, to],
    queryFn: () => api.get(`/calendar${qs}`),
    enabled: Boolean(from && to),
    placeholderData: keepPreviousData,
  });
}

/**
 * Review queue: the owner's tasks flagged requires_review (Requirement 8.3).
 */
export function useReviewQueue() {
  return useQuery({
    queryKey: ['review-queue'],
    queryFn: () => api.get('/review-queue'),
  });
}

/**
 * The owner's uploaded contracts (newest first), optionally filtered by status.
 * Backs the Contracts / Analysis hub page (GET /contracts).
 *
 * @param {{ status?: string }} [filters]
 */
export function useContracts(filters = {}) {
  const qs = buildQueryString(filters);
  return useQuery({
    queryKey: ['contracts', filters],
    queryFn: () => api.get(`/contracts${qs}`),
    placeholderData: keepPreviousData,
  });
}

/**
 * Mutation to delete a contract (and, via DB cascade + storage cleanup, all of its
 * clauses/tasks/reminders/notifications and the stored file). Invalidates every list that
 * could contain the contract or its tasks so the UI updates immediately.
 */
export function useDeleteContract() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (contractId) => api.delete(`/contracts/${contractId}`),
    onSuccess: () => {
      for (const key of [
        ['contracts'],
        ['tasks'],
        ['dashboard-summary'],
        ['calendar'],
        ['review-queue'],
      ]) {
        queryClient.invalidateQueries({ queryKey: key });
      }
    },
  });
}

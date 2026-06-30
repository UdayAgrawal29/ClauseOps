import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

/**
 * Fetch a contract's full analysis: the contract plus its `clauses` and `tasks`
 * (each task carries `source_text` and the span offsets that ground it).
 *
 * Backed by `GET /contracts/{id}` (design §Component 2, Requirement 5.4). The
 * response is owner-scoped by the backend, so a non-owned id resolves to a
 * not-authorized/not-found error surfaced via `error`.
 *
 * @param {string|number|null|undefined} contractId
 * @returns {import('@tanstack/react-query').UseQueryResult<object>}
 */
export function useContractAnalysis(contractId) {
  return useQuery({
    queryKey: ['contract-analysis', contractId],
    queryFn: () => api.get(`/contracts/${contractId}`),
    enabled: Boolean(contractId),
  });
}

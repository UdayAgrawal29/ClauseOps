import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, contractProgressSocketUrl } from '../lib/api';
import { useAuthStore } from '../store/auth';

// Terminal contract statuses — once reached, progress stops (no more socket/polling).
export const TERMINAL_STATUSES = ['COMPLETE', 'FAILED'];

const POLL_INTERVAL_MS = 2000;

function isTerminal(status) {
  return TERMINAL_STATUSES.includes(status);
}

// Monotonic rank of a progress snapshot so we can always surface the furthest-along
// state regardless of which transport (socket snapshot vs polling) delivered it.
// Terminal > PROCESSING > PENDING; ties broken by progress_pct. Higher wins.
function progressRank(s) {
  if (!s || typeof s !== 'object') return -1;
  const pct = typeof s.progress_pct === 'number' ? s.progress_pct : 0;
  if (s.status === 'COMPLETE' || s.status === 'FAILED') return 1_000_000;
  if (s.status === 'PROCESSING') return 1000 + pct;
  if (s.status === 'PENDING') return pct; // 0..100, below any PROCESSING
  return pct; // unknown/null status — treat like PENDING
}

// Return whichever snapshot is furthest along (see progressRank). Falls back
// gracefully when one (or both) side is null.
function pickFurthest(a, b) {
  if (!a) return b || null;
  if (!b) return a || null;
  return progressRank(a) >= progressRank(b) ? a : b;
}

/**
 * Track live processing progress for a contract.
 *
 * Strategy (design §Component 5 — Progress Channel):
 *   1. Prefer the WebSocket `WS /ws/contracts/{id}?token=<accessJWT>`. Each message is
 *      `{ stage, progress_pct, status }` and updates the UI immediately.
 *   2. If the socket cannot open, errors, or closes unexpectedly BEFORE a terminal status,
 *      fall back to polling `GET /contracts/{id}/status` via TanStack Query until the
 *      contract reaches COMPLETE or FAILED.
 *
 * The hook is resilient to either transport disappearing and never double-counts a
 * terminal status.
 *
 * @param {string|number|null|undefined} contractId
 * @returns {{
 *   stage: string|null,
 *   progressPct: number,
 *   status: string|null,
 *   source: 'connecting'|'socket'|'polling',
 *   isComplete: boolean,
 *   isFailed: boolean,
 *   error: Error|null,
 *   raw: object|null,
 * }}
 */
export function useContractProgress(contractId) {
  // Read the access token defensively from the auth store; the socket handshake carries it
  // as a query param because browsers cannot set Authorization headers on WS upgrades.
  const token = useAuthStore((s) => s.accessToken);

  const [socketState, setSocketState] = useState(null); // { stage, progress_pct, status }
  const [usePolling, setUsePolling] = useState(false);
  const [source, setSource] = useState('connecting');

  // `doneRef` guards against firing the polling fallback when the socket closes because we
  // intentionally closed it (terminal status reached or component unmounted).
  const doneRef = useRef(false);

  useEffect(() => {
    if (!contractId) {
      return undefined;
    }

    doneRef.current = false;
    setSocketState(null);
    setUsePolling(false);
    setSource('connecting');

    let ws = null;
    let cancelled = false;

    const startPolling = () => {
      if (doneRef.current || cancelled) return;
      setUsePolling(true);
      setSource('polling');
    };

    const attachHandlers = (socket) => {
      socket.onopen = () => {
        if (!doneRef.current && !cancelled) setSource('socket');
      };
      socket.onmessage = (event) => {
        let data;
        try {
          data = JSON.parse(event.data);
        } catch {
          return; // ignore malformed frames
        }
        setSocketState(data);
        setSource('socket');
        if (isTerminal(data.status)) {
          doneRef.current = true;
          try {
            socket.close();
          } catch {
            /* noop */
          }
        }
      };
      socket.onerror = () => startPolling();
      socket.onclose = () => {
        // Unexpected close before completion -> fall back to polling.
        if (!doneRef.current && !cancelled) startPolling();
      };
    };

    const connect = async () => {
      // Prefer a short-lived single-purpose WS ticket so the long-lived access
      // token is never placed in the socket URL (which can leak via logs). Fall
      // back to the in-memory access token if the ticket request fails.
      let wsToken = token || undefined;
      try {
        const ticket = await api.post('/auth/ws-ticket');
        if (ticket && ticket.access_token) wsToken = ticket.access_token;
      } catch {
        /* fall back to the in-memory access token */
      }
      if (cancelled) return;
      try {
        ws = new WebSocket(contractProgressSocketUrl(contractId, wsToken));
      } catch {
        startPolling();
        return;
      }
      attachHandlers(ws);
    };

    connect();

    return () => {
      // Mark cancelled/done so the close handler does not trigger a fallback on
      // unmount and a socket opened after an await is still cleaned up.
      cancelled = true;
      doneRef.current = true;
      if (ws) {
        try {
          ws.close();
        } catch {
          /* noop */
        }
      }
    };
  }, [contractId, token]);

  const socketTerminal = Boolean(socketState && isTerminal(socketState.status));

  const pollingQuery = useQuery({
    queryKey: ['contract-status', contractId],
    queryFn: () => api.get(`/contracts/${contractId}/status`),
    enabled: Boolean(contractId) && usePolling && !socketTerminal,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data && isTerminal(data.status)) return false;
      return POLL_INTERVAL_MS;
    },
  });

  // Choose the authoritative state by *progress*, not by transport.
  //
  // The socket emits a one-off snapshot of the contract's state at connect time
  // (often PENDING/0%) and may then close early (the Redis pub/sub stream can
  // end before any stage update arrives). If we naively preferred the socket
  // state, that stale PENDING/0% snapshot would shadow the live polling results
  // forever and the bar would freeze even though the backend is progressing.
  //
  // Instead we always surface whichever source is *furthest along*: a terminal
  // status beats everything, a PROCESSING beats PENDING, and within a status the
  // higher progress_pct wins. This is monotonic with how the pipeline actually
  // advances, so neither transport can ever drag the UI backwards.
  const merged = pickFurthest(socketState, pollingQuery.data);
  const progressPct =
    merged && typeof merged.progress_pct === 'number' ? merged.progress_pct : 0;

  return {
    stage: merged?.stage ?? null,
    progressPct,
    status: merged?.status ?? null,
    source,
    isComplete: merged?.status === 'COMPLETE',
    isFailed: merged?.status === 'FAILED',
    error: pollingQuery.error || null,
    raw: merged,
  };
}

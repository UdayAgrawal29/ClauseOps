import { QueryClient } from '@tanstack/react-query';

// Shared TanStack Query client. Tuned for the async pipeline: server state (contracts,
// tasks, dashboard) is cached and polled; live progress prefers the WebSocket with polling
// as a fallback (task 17).
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

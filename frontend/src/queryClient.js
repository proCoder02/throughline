import { QueryClient } from '@tanstack/react-query';

// Shared across the whole app -- one cache, so e.g. a task fetched on the
// Tasks screen and referenced from a "view source conversation" link
// elsewhere doesn't trigger a redundant re-fetch. Query functions must
// still call through api.js's `api`/`apiJson` (never fetch() directly), so
// the existing 401 -> forced-logout handler (setUnauthorizedHandler) keeps
// working unchanged for every query, not just the hand-written fetches
// that predate TanStack Query.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

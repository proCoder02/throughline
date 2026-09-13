import React from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { router } from './router.jsx';
import { queryClient } from './queryClient.js';
import './theme.css';
// Tailwind + the shadcn/ui token bridge -- imported after theme.css so its
// utility classes read the app's real, already-verified palette (see
// tailwind-bridge.css's own header comment) rather than Tailwind defaults.
import './tailwind.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>
);

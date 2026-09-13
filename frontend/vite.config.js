import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const FLASK = 'http://129.213.21.239';

// Existing Flask routes are unprefixed (no /api), so the dev proxy forwards
// each one by exact path instead of renaming the whole backend surface.
// /insights, /mood, /search were missing here (a real gap, not deliberate --
// see the frontend rewrite plan) until the Insights screen/command palette
// needed them.
const PROXIED_PATHS = [
  '/login', '/register', '/logout', '/me', '/settings',
  '/conversations', '/chat', '/tasks', '/profiles',
  '/friends', '/speakers', '/analyze', '/save', '/transcribe',
  '/persona', '/categories', '/calls', '/devices',
  // Object storage (Cloudflare R2) -- see MEDIA_STORAGE_PLAN.md.
  '/uploads', '/profile',
  '/insights', '/mood', '/search',
];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Matches shadcn/ui's own generated import convention (@/components/...,
    // @/lib/utils) -- required for `npx shadcn add <component>` output to
    // resolve without hand-editing every generated file's imports.
    alias: { '@': path.resolve(__dirname, './src') },
  },
  // Built assets land under Flask's existing /static handling (no new
  // Flask route needed for JS/CSS) -- only index.html itself needs a
  // dedicated route, added in app.py.
  base: '/static/app/',
  server: {
    port: 5173,
    proxy: {
      ...Object.fromEntries(PROXIED_PATHS.map((p) => [p, { target: FLASK, changeOrigin: true }])),
      '/ws': { target: FLASK, ws: true, changeOrigin: true },
    },
  },
  build: { outDir: '../static/app', emptyOutDir: true },
});

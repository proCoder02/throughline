import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const FLASK = 'http://129.213.21.239';

// Existing Flask routes are unprefixed (no /api), so the dev proxy forwards
// each one by exact path instead of renaming the whole backend surface.
const PROXIED_PATHS = [
  '/login', '/register', '/logout', '/me', '/settings',
  '/conversations', '/chat', '/tasks', '/profiles',
  '/friends', '/speakers', '/analyze', '/save', '/transcribe',
  '/persona', '/categories', '/calls', '/devices',
];

export default defineConfig({
  plugins: [react()],
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

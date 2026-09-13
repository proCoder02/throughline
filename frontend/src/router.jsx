import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate, useOutletContext } from 'react-router-dom';
import App from './App.jsx';
import ChatsSection from './components/ChatsSection.jsx';

// Phase 1 of the frontend rewrite (see the approved plan): map every route
// 1:1 to today's five sections with zero visual/behavioral change. Route
// names match today's existing section keys exactly ('chats', 'tasks', ...)
// -- the IA rename to Home/Messages/People/Ask/Insights/Calls is deliberately
// a LATER phase, not bundled into this plumbing-only change.
//
// Every client route lives under /app -- NOT bare paths like /tasks or
// /friends. This backend's API is deliberately unprefixed (no /api, see
// vite.config.js's own comment), so bare /tasks, /profiles, /friends,
// /settings, and (in a later phase) /calls are ALL already real, existing
// Flask API routes. Confirmed live: without the /app prefix, a direct
// browser visit to e.g. /tasks hits the JSON tasks API (401 unauthenticated)
// instead of ever reaching the SPA fallback -- Werkzeug correctly prefers
// the more specific, already-registered API route over the root catch-all,
// exactly as that route's own docstring predicted for shadowing in general,
// just not for this specific same-path-different-purpose collision. The
// /app prefix (same convention CostLens's own frontend uses) sidesteps the
// whole class of collision rather than requiring a case-by-case check
// against the backend's route table for every future path.
//
// ChatsSection stays eager (not lazy): the "os" screen's Listen tab embeds
// it directly (see os/OsApp.jsx), and it's also still reachable at its own
// /app/chats route (kept, not deleted -- see the index redirect below), so
// keeping it in the main bundle avoids a double fetch either way. The other
// four keep their existing lazy() split.
const TasksSection = lazy(() => import('./components/TasksSection.jsx'));
const ProfilesSection = lazy(() => import('./components/ProfilesSection.jsx'));
const FriendsSection = lazy(() => import('./components/FriendsSection.jsx'));
const SettingsSection = lazy(() => import('./components/SettingsSection.jsx'));

// The "Personal Intelligence OS" screen -- now the default app (see the
// index/catch-all redirects below). Nested under /app like every other
// section so it gets auth/notify/call/theme for free via useOutletContext --
// App.jsx hides the old IconRail specifically for this route since OsApp
// renders its own full sidebar instead.
//
// The old chats/tasks/profiles/friends/settings routes are deliberately
// NOT removed -- they stay reachable directly (e.g. /app/chats) as a live
// rollback path. If something in the new screen needs to be reverted, the
// fix is changing the redirects below back to /app/chats, not restoring
// deleted code.
const OsApp = lazy(() => import('./os/OsApp.jsx'));

// Same fallback markup App.jsx's old shared <Suspense> used, per-route now
// instead of one boundary wrapping all four -- no observable difference
// since only one section is ever mounted at a time.
const LAZY_FALLBACK = <div className="detail-pane" style={{ flex: 1 }} />;

function ChatsRoute() {
  const { notify, pendingConversationId, consumePendingConversationId } = useOutletContext();
  return (
    <ChatsSection
      notify={notify}
      openConversationId={pendingConversationId}
      onConsumeOpenConversationId={consumePendingConversationId}
    />
  );
}

function TasksRoute() {
  const { notify, openConversationInChats } = useOutletContext();
  return (
    <Suspense fallback={LAZY_FALLBACK}>
      <TasksSection notify={notify} onOpenConversation={openConversationInChats} />
    </Suspense>
  );
}

function ProfilesRoute() {
  const { openConversationInChats } = useOutletContext();
  return (
    <Suspense fallback={LAZY_FALLBACK}>
      <ProfilesSection onOpenConversation={openConversationInChats} />
    </Suspense>
  );
}

function FriendsRoute() {
  const { call, notify, auth } = useOutletContext();
  return (
    <Suspense fallback={LAZY_FALLBACK}>
      <FriendsSection onStartCall={call.startCall} notify={notify} myUserId={auth.user.id} />
    </Suspense>
  );
}

function SettingsRoute() {
  const { auth, notify, theme, toggleTheme } = useOutletContext();
  return (
    <Suspense fallback={LAZY_FALLBACK}>
      <SettingsSection
        user={auth.user} onLogout={auth.logout} onUpdateUser={auth.updateUser}
        online={notify.connected} theme={theme} onToggleTheme={toggleTheme}
      />
    </Suspense>
  );
}

function OsRoute() {
  const ctx = useOutletContext();
  return (
    <Suspense fallback={LAZY_FALLBACK}>
      <OsApp {...ctx} />
    </Suspense>
  );
}

export const router = createBrowserRouter([
  { path: '/', element: <Navigate to="/app/os" replace /> },
  {
    path: '/app',
    element: <App />,
    children: [
      { index: true, element: <Navigate to="/app/os" replace /> },
      { path: 'chats', element: <ChatsRoute /> },
      { path: 'tasks', element: <TasksRoute /> },
      { path: 'profiles', element: <ProfilesRoute /> },
      { path: 'friends', element: <FriendsRoute /> },
      { path: 'settings', element: <SettingsRoute /> },
      { path: 'os', element: <OsRoute /> },
      // Anything unrecognized under /app (including a stale bookmark to a
      // future IA path not built yet) falls back to the default section
      // rather than a blank/error screen.
      { path: '*', element: <Navigate to="/app/os" replace /> },
    ],
  },
  // Anything outside /app entirely (not a route this SPA owns at all) also
  // lands on the default section rather than relying on the Flask fallback
  // alone to guess -- see app.py's spa_fallback for why /app exists.
  { path: '*', element: <Navigate to="/app/os" replace /> },
]);

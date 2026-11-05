import { useCallback, useEffect, useState } from 'react';
import { apiJson, getToken, setToken, clearToken, setUnauthorizedHandler } from '../api.js';

// The last signed-in user, kept so the app can draw straight away on the
// next visit instead of a blank screen while /me answers. /me still runs
// every load and wins: a 401 signs out, fresh data replaces the cache.
const USER_CACHE_KEY = 'tl_user';

function readCachedUser() {
  try {
    if (!getToken()) return null;
    const raw = localStorage.getItem(USER_CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeCachedUser(user) {
  try {
    if (user) localStorage.setItem(USER_CACHE_KEY, JSON.stringify({
      id: user.id, username: user.username, profile_picture_url: user.profile_picture_url ?? null,
    }));
    else localStorage.removeItem(USER_CACHE_KEY);
  } catch { /* storage blocked -- the app still works, just without the instant start */ }
}

export function useAuth() {
  const [user, setUserState] = useState(readCachedUser);
  const [loading, setLoading] = useState(() => readCachedUser() === null);

  const setUser = useCallback((u) => {
    setUserState((prev) => {
      const next = typeof u === 'function' ? u(prev) : u;
      writeCachedUser(next);
      return next;
    });
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    apiJson('/me')
      .then((data) => setUser(data))
      .catch((e) => {
        // Only a real "not signed in" ends the session; a network blip keeps
        // the cached user rather than throwing them back to the login screen.
        if (e.status === 401) { clearToken(); setUser(null); }
      })
      .finally(() => setLoading(false));
  }, [setUser]);

  const login = useCallback(async (username, password) => {
    const data = await apiJson('/login', { method: 'POST', body: JSON.stringify({ username, password }) });
    setToken(data.token);
    setUser(data);
    return data;
  }, [setUser]);

  const register = useCallback(async (username, email, password, personalization, acceptedTerms = false) => {
    const data = await apiJson('/register', {
      method: 'POST',
      body: JSON.stringify({ username, email, password, personalization, accepted_terms: acceptedTerms === true }),
    });
    setToken(data.token);
    setUser(data);
    return data;
  }, [setUser]);

  const logout = useCallback(async () => {
    await apiJson('/logout', { method: 'POST' }).catch(() => {});
    clearToken();
    setUser(null);
  }, [setUser]);

  // Patches the in-memory user (e.g. a freshly-uploaded profile picture)
  // without a round-trip back to /me -- the upload's own response already
  // has the new URL.
  const updateUser = useCallback((patch) => {
    setUser((u) => (u ? { ...u, ...patch } : u));
  }, [setUser]);

  return { user, loading, login, register, logout, updateUser };
}

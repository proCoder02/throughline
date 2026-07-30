import { useCallback, useEffect, useState } from 'react';
import { apiJson, setToken, clearToken, setUnauthorizedHandler } from '../api.js';

export function useAuth() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    apiJson('/me').then((data) => setUser(data)).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username, password) => {
    const data = await apiJson('/login', { method: 'POST', body: JSON.stringify({ username, password }) });
    setToken(data.token);
    setUser(data);
    return data;
  }, []);

  const register = useCallback(async (username, email, password, personalization) => {
    const data = await apiJson('/register', {
      method: 'POST',
      body: JSON.stringify({ username, email, password, personalization }),
    });
    setToken(data.token);
    setUser(data);
    return data;
  }, []);

  const logout = useCallback(async () => {
    await apiJson('/logout', { method: 'POST' }).catch(() => {});
    clearToken();
    setUser(null);
  }, []);

  return { user, loading, login, register, logout };
}

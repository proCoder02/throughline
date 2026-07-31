const TOKEN_KEY = 'authToken';

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (token) => { if (token) localStorage.setItem(TOKEN_KEY, token); };
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

export async function api(path, options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers['Authorization'] = 'Bearer ' + token;
  // FormData needs fetch to set its own Content-Type (with the boundary) --
  // forcing application/json on it would break multipart uploads.
  if (options.body && !headers['Content-Type'] && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const res = await fetch(path, { ...options, headers });
  if (res.status === 401 && path !== '/me') {
    clearToken();
    onUnauthorized();
  }
  return res;
}

export async function apiJson(path, options) {
  const res = await api(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || 'Request failed'), { status: res.status, data });
  return data;
}

export const post = (path, body) => apiJson(path, { method: 'POST', body: JSON.stringify(body) });
export const del = (path) => apiJson(path, { method: 'DELETE' });
export const postForm = (path, formData) => apiJson(path, { method: 'POST', body: formData });

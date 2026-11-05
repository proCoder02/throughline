// Consent notices the server tracks per user and version (see the backend's
// legal/__init__.py): the recording notice, shown once before the first
// Listen or call, and the Terms/Privacy agreement. Pub/sub singleton like
// lib/notify.js, so the recording hooks can ask without a Provider;
// ConsentHost (mounted once in App.jsx) renders the dialog.
import { apiJson, post } from '../api.js';

let status = null; // last /consents answer, or null when unknown
let inflight = null;
let listener = null;

export function subscribeConsent(fn) {
  listener = fn;
  return () => { if (listener === fn) listener = null; };
}

export function getConsentStatus(force = false) {
  if (status && !force) return Promise.resolve(status);
  if (!inflight) {
    inflight = apiJson('/consents')
      .then((s) => { status = s; return s; })
      .finally(() => { inflight = null; });
  }
  return inflight;
}

export async function acceptConsent(kind) {
  const s = await post('/consents', { kind });
  status = s;
  return s;
}

export function resetConsentCache() { status = null; }

/// Resolves true once the user has accepted the current recording notice
/// (immediately if they already had), false if they cancel. If the status
/// can't be loaded, the notice is shown anyway -- never skipped silently.
export async function ensureRecordingNotice() {
  let needed = true;
  try {
    const s = await getConsentStatus();
    needed = !!s?.recording_notice?.needed;
  } catch { /* offline/unknown: ask */ }
  if (!needed) return true;
  if (!listener) return window.confirm('Recordings are transcribed. Tell everyone you record and get their consent where the law requires it. Continue?');
  return new Promise((resolve) => listener({ kind: 'recording_notice', resolve }));
}

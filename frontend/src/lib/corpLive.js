// Live work-message alerts, Teams-style: unread counts for badges (sidebar,
// Personal/Corporate switch, tab title), a toast, and a desktop
// notification when the tab is in the background.
//
// Events arrive on the app's existing notify socket (useNotifications
// forwards every 'corp_*' event as a 'tl:corp' window event); /corp/unread
// gives the starting counts and re-syncs after reads.
import { useSyncExternalStore } from 'react';
import { api } from '../api.js';

let state = { total: 0, byOrg: {} };
// Newest message already alerted for. The first sync only records it: old unread doesn't pop up.
let lastAlerted = 0;
let synced = false;
const subs = new Set();
const emit = () => subs.forEach((f) => f());
const set = (next) => { state = next; emit(); };

export async function refreshUnread() {
  try {
    const res = await api('/corp/unread');
    if (!res.ok) return; // Corporate switched off, or signed out
    const d = await res.json();
    set({ total: d.total || 0, byOrg: d.by_org || {} });
    // A message the live socket didn't deliver (it dropped, or a sleeping tab): alert for it now.
    if (d.latest && d.latest.message_id > lastAlerted) {
      if (synced) window.dispatchEvent(new CustomEvent('tl:corp', { detail: { type: 'corp_message', ...d.latest } }));
      lastAlerted = d.latest.message_id;
    }
    synced = true;
  } catch { /* offline: keep the last counts */ }
}

export function useCorpUnread() {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => state);
}

// Called by useNotifications for each live work event.
export function onCorpEvent(msg) {
  if (msg.type === 'corp_message') {
    if (msg.message_id <= lastAlerted) return; // already shown via the poll
    lastAlerted = Math.max(lastAlerted, msg.message_id);
    const viewing = window.location.pathname.startsWith('/app/corp/messages')
      && new URLSearchParams(window.location.search).get('t') === String(msg.thread_id)
      && document.visibilityState === 'visible';
    if (!viewing) {
      set({ total: state.total + 1, byOrg: { ...state.byOrg, [msg.org_id]: (state.byOrg[msg.org_id] || 0) + 1 } });
    }
  }
  window.dispatchEvent(new CustomEvent('tl:corp', { detail: msg }));
}

export const desktopAlertsState = () => (typeof Notification === 'undefined' ? 'unsupported' : Notification.permission);
export const askDesktopAlerts = () => (typeof Notification === 'undefined' ? Promise.resolve('unsupported') : Notification.requestPermission());

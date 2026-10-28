import { useEffect, useState } from 'react';
import { apiJson } from '../api.js';

// Outlook mail link status ({enabled, connected, needs_reconnect, email,
// connected_at}), shared across every screen that cares -- both Settings
// cards, both chat surfaces' suggestion chips and "Checking your inbox…"
// indicator, and App.jsx's post-OAuth toast. A module-level store rather
// than per-component fetches, so a connect/disconnect in one place updates
// the rest without each screen refetching on its own (same pub/sub-
// singleton shape as lib/notify.js).
let status = null; // null = not loaded yet
let inflight = null;
let listeners = [];

function emit() {
  listeners.forEach((fn) => fn(status));
}

export function refreshOutlookStatus() {
  inflight = apiJson('/integrations/outlook/status')
    .then((s) => { status = s; })
    .catch(() => { status = status || { enabled: false }; })
    .finally(() => { inflight = null; emit(); });
  return inflight;
}

export function useOutlookStatus() {
  const [value, setValue] = useState(status);
  useEffect(() => {
    listeners.push(setValue);
    if (status === null && !inflight) refreshOutlookStatus();
    return () => { listeners = listeners.filter((l) => l !== setValue); };
  }, []);
  return value;
}

/** Usable for chat right now -- connected and not needing a reconnect. */
export function outlookReady(s) {
  return !!(s?.enabled && s.connected && !s.needs_reconnect);
}

// Mirrors outlook/adapter.py's _MAIL_INTENT_RE -- only decides which
// waiting label to show, never whether the backend uses mail.
const MAIL_INTENT_RE = /\b(?:inbox|outlook|e-?mails?|mails?|unread)\b/i;

export function looksLikeMailQuestion(text) {
  return MAIL_INTENT_RE.test(text || '');
}

export function pendingReplyLabel(s, text) {
  return outlookReady(s) && looksLikeMailQuestion(text) ? 'Checking your inbox…' : 'Thinking…';
}

export const OUTLOOK_EXAMPLE_PROMPTS = [
  'Summarize my unread emails',
  'Anything urgent in my inbox today?',
  'What did I get emailed about this week?',
];

// Where a user fully revokes the app on Microsoft's side -- Disconnect here
// only deletes our copy of the tokens.
export const MICROSOFT_REVOKE_LINKS = [
  { label: 'Personal account', href: 'https://account.live.com/consent/Manage' },
  { label: 'Work or school account', href: 'https://myapps.microsoft.com' },
];

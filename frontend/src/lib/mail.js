import { useEffect, useState } from 'react';
import { apiJson } from '../api.js';

// Linked-mailbox status for every provider ({outlook: {...}, gmail: {...}},
// each {enabled, connected, needs_reconnect, email, connected_at, ...}),
// shared across every screen that cares -- the Settings cards, both chat
// surfaces' suggestion chips and "Checking your inbox…" label, and
// App.jsx's post-OAuth toast. A module-level store rather than per-component
// fetches, so a connect/disconnect in one place updates the rest (same
// pub/sub-singleton shape as lib/notify.js).
export const MAIL_PROVIDERS = {
  outlook: {
    label: 'Outlook',
    blurb: 'Connect your Outlook or Microsoft 365 mailbox, then ask about your email right in chat.',
    revokeLinks: [
      { label: 'Personal account', href: 'https://account.live.com/consent/Manage' },
      { label: 'Work or school account', href: 'https://myapps.microsoft.com' },
    ],
    signedOutReason: 'Microsoft signed this connection out (a password change or expired sign-in does this).',
  },
  gmail: {
    label: 'Gmail',
    blurb: 'Connect your Gmail, then ask about your email right in chat.',
    revokeLinks: [{ label: 'Google account connections', href: 'https://myaccount.google.com/connections' }],
    signedOutReason: 'Google signed this connection out (it does this weekly while our Google access is still being verified, or after a password change).',
  },
};
export const PROVIDER_KEYS = Object.keys(MAIL_PROVIDERS);

let statuses = null; // null = not loaded yet
let inflight = null;
let listeners = [];

function emit() {
  listeners.forEach((fn) => fn(statuses));
}

export function refreshMailStatus() {
  inflight = Promise.all(PROVIDER_KEYS.map((p) => apiJson(`/integrations/${p}/status`).catch(() => ({ enabled: false }))))
    .then((list) => { statuses = Object.fromEntries(PROVIDER_KEYS.map((p, i) => [p, list[i]])); })
    .finally(() => { inflight = null; emit(); });
  return inflight;
}

export function useMailStatus() {
  const [value, setValue] = useState(statuses);
  useEffect(() => {
    listeners.push(setValue);
    if (statuses === null && !inflight) refreshMailStatus();
    return () => { listeners = listeners.filter((l) => l !== setValue); };
  }, []);
  return value;
}

/** This provider is connected and usable for chat right now. */
export function providerReady(s) {
  return !!(s?.enabled && s.connected && !s.needs_reconnect);
}

/** At least one mailbox is usable for chat right now. */
export function anyMailReady(all) {
  return PROVIDER_KEYS.some((p) => providerReady(all?.[p]));
}

// Mirrors mail_common._MAIL_INTENT_RE -- only picks the waiting label,
// never whether the backend uses mail.
const MAIL_INTENT_RE = /\b(?:inbox|outlook|gmail|e-?mails?|mails?|unread)\b/i;

export function pendingReplyLabel(all, text) {
  return anyMailReady(all) && MAIL_INTENT_RE.test(text || '') ? 'Checking your inbox…' : 'Thinking…';
}

export const MAIL_EXAMPLE_PROMPTS = [
  'Summarize my unread emails',
  'Anything urgent in my inbox today?',
  'What did I get emailed about this week?',
];

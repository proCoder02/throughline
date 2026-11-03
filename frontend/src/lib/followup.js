// Whether a global-chat message leans on earlier turns ("and what did he
// say?", "the second one", "which company?") -- only to pick the waiting
// text, so a slightly longer wait for a follow-up reads as intended. A loose
// mirror of the server's resolver gate (chat_followup.needs_resolution);
// getting it wrong only changes a label, never what the server does.
const FOLLOWUP_RE = /^\s*(?:and|also|plus|then|so|what about|how about|same)\b|\b(?:he|him|his|she|her|hers|they|them|their|it|that|this|those|these|there|one|ones|first|second|third|last|latest|previous|earlier|same|again|else|instead)\b/i;

export function looksLikeFollowUp(text, hasEarlierReplies) {
  const t = (text || '').trim();
  if (!t || !hasEarlierReplies) return false;
  return FOLLOWUP_RE.test(t) || t.split(/\s+/).length <= 5;
}

export const FOLLOWUP_PENDING_LABEL = 'Checking what we talked about…';

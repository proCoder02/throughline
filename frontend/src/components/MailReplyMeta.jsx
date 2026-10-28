import { useState } from 'react';
import { MAIL_PROVIDERS } from '../lib/mail.js';
import '../styles/mail.css';

// Sources/activity saved before Gmail existed carry no provider -- they were Outlook.
const providerOf = (x) => (x?.provider && MAIL_PROVIDERS[x.provider] ? x.provider : 'outlook');

// Gmail links saved by the first release used /mail/u/?authuser=<addr>#all/<id>,
// whose authuser redirect can drop the #fragment -- rewrite them to the
// /mail/u/<addr>/#all/<id> form new replies already carry.
const OLD_GMAIL_LINK_RE = /^https:\/\/mail\.google\.com\/mail\/u\/\?authuser=([^#]+)#all\/(.+)$/;
function webLink(source) {
  const m = OLD_GMAIL_LINK_RE.exec(source.web_link || '');
  return m ? `https://mail.google.com/mail/u/${decodeURIComponent(m[1])}/#all/${m[2]}` : source.web_link;
}

const VISIBLE_SOURCES = 4;

function senderName(from) {
  // "Name <addr>" -> "Name"; bare address stays as-is.
  const match = /^(.*?)\s*<[^>]+>$/.exec(from || '');
  return (match ? match[1] : from) || 'Unknown sender';
}

function shortDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function SourceChip({ source }) {
  const body = (
    <>
      <span className="mail-source-top">
        <span className="mail-source-from">{senderName(source.from)}</span>
        <span className="mail-source-date">{shortDate(source.received)}</span>
      </span>
      <span className="mail-source-subject">{source.subject || '(no subject)'}</span>
      {source.read && <span className="mail-source-badge">Opened</span>}
    </>
  );
  const title = `${source.from || ''} — ${source.subject || ''}`;
  const where = MAIL_PROVIDERS[providerOf(source)].label;
  return source.web_link ? (
    <a className="mail-source" href={webLink(source)} target="_blank" rel="noopener noreferrer" title={`${title} (opens in ${where})`}>
      {body}
    </a>
  ) : (
    <span className="mail-source" title={title}>{body}</span>
  );
}

/// Shown under a global-chat reply that used the Outlook tools: what the
/// assistant actually did ("Searched your mailbox for …") and the emails
/// it drew on, each opening in Outlook -- so a mail answer is visibly
/// grounded and checkable, never indistinguishable from a guess.
export default function MailReplyMeta({ meta }) {
  const [expanded, setExpanded] = useState(false);
  if (!meta) return null;
  const activity = meta.tool_activity || [];
  const sources = meta.mail_sources || [];
  if (!activity.length && !sources.length) return null;
  const shown = expanded ? sources : sources.slice(0, VISIBLE_SOURCES);

  return (
    <div className="mail-ui mail-reply-meta">
      {activity.map((a, i) => (
        <div key={i} className={'mail-activity' + (a.error ? ' error' : '')}>
          <span aria-hidden="true">{a.error ? '⚠️' : '📧'}</span>
          <span>{a.label}</span>
          {a.error && <a className="mail-activity-action" href={`/integrations/${providerOf(a)}/connect`}>Reconnect</a>}
        </div>
      ))}
      {sources.length > 0 && (
        <>
          <div className="mail-sources-label">Sources</div>
          <div className="mail-sources">
            {shown.map((s) => <SourceChip key={`${providerOf(s)}:${s.id}`} source={s} />)}
          </div>
          {sources.length > VISIBLE_SOURCES && (
            <button type="button" className="mail-sources-toggle" onClick={() => setExpanded((v) => !v)}>
              {expanded ? 'Show fewer' : `Show all ${sources.length}`}
            </button>
          )}
        </>
      )}
    </div>
  );
}

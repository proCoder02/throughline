import { MailIcon } from '../icons.jsx';
import { MAIL_PROVIDERS } from '../lib/mail.js';

function senderName(from) {
  const match = /^(.*?)\s*<[^>]+>$/.exec(from || '');
  return (match ? match[1] : from) || '';
}

/// "From email: <subject> · <sender>" -- shown on a task or expense the
/// assistant created from an email (task.source_email / card.source_email),
/// opening that email in Outlook/Gmail. Renders nothing without a source.
export default function EmailSourceLink({ source, compact = false, style }) {
  if (!source?.subject && !source?.web_link) return null;
  const label = MAIL_PROVIDERS[source.provider]?.label || 'email';
  const who = senderName(source.from);
  const text = (
    <>
      <MailIcon style={{ width: 13, height: 13, flexShrink: 0 }} />
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {compact ? '' : 'From email: '}{source.subject || '(no subject)'}{who ? ` · ${who}` : ''}
      </span>
    </>
  );
  const common = {
    style: { display: 'inline-flex', alignItems: 'center', gap: 5, maxWidth: '100%', fontSize: 12, ...style },
    title: `${source.subject || ''}${who ? ' — ' + who : ''}${source.web_link ? ` (opens in ${label})` : ''}`,
  };
  return source.web_link ? (
    <a {...common} href={source.web_link} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
      {text}
    </a>
  ) : (
    <span {...common}>{text}</span>
  );
}

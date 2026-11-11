// Opt in to "promises from my work email" (the mailbox connected in Personal).
import { useEffect, useState } from 'react';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { TagIcon } from '../../../icons.jsx';
import { TagToTicket } from './tickets.jsx';

// Recent work emails Throughline has read for me, each taggable to a ticket.
function RecentEmails({ corp }) {
  const orgId = corp.data.org.id;
  const [list, setList] = useState(null);
  const [tagging, setTagging] = useState(null);
  useEffect(() => { corpApi.myEmails(orgId).then((d) => setList(d.emails)).catch(() => setList([])); }, [orgId]);
  if (!list || list.length === 0) return null;
  return (
    <details className="tk-emails">
      <summary>Recent work emails ({list.length}) · tag one to a ticket</summary>
      <ul className="corp-list">{list.map((e) => (
        <li key={e.id} className="corp-person">
          <span className="corp-row-main"><span className="corp-row-title">{e.subject || '(no subject)'}</span>
            <span className="corp-row-sub">{e.folder === 'sent' ? 'Sent' : `From ${e.sender}`} · {new Date(e.created_at).toLocaleDateString()}</span></span>
          <button className="corp-btn small" onClick={() => setTagging(e.id)}><TagIcon aria-hidden="true" /> Tag</button>
        </li>))}</ul>
      {tagging && <TagToTicket corp={corp} sourceType="email" sourceId={tagging} onClose={() => setTagging(null)} />}
      <p className="corp-muted corp-small">Emails with a ticket key like PAY-12 in the subject or text are linked automatically.</p>
    </details>
  );
}

export default function WorkEmail({ corp }) {
  const [s, setS] = useState(null);
  const orgId = corp.data.org.id;
  useEffect(() => { corpApi.mailLink(orgId).then(setS).catch(() => {}); }, [orgId]);
  if (!s) return null;
  const toggle = async (on) => { try { setS(await corpApi.setMailLink(orgId, on)); } catch (e) { showToast(e.message, 'error'); } };
  return (
    <section className="corp-card corp-form">
      <h2>Promises from your work email</h2>
      <p className="corp-muted corp-small">
        Throughline reads a few new emails you send and receive, about every 15 minutes. Promises in them appear in your
        Commitments marked “To confirm”; nothing is shared or added without you. Later emails count as evidence that work was done.
      </p>
      {s.connected.length === 0 ? (
        <p className="corp-warn corp-small">Connect Gmail or Outlook first: switch to Personal, then Settings, Email.</p>
      ) : (
        <label className="consent-check"><input type="checkbox" checked={s.enabled} onChange={(e) => toggle(e.target.checked)} />
          {' '}Find promises in my {s.connected.map((p) => (p === 'gmail' ? 'Gmail' : 'Outlook')).join(' and ')}</label>
      )}
      {s.enabled && <p className="corp-muted corp-small">{s.read} emails read so far.</p>}
      {s.enabled && <RecentEmails corp={corp} />}
    </section>
  );
}

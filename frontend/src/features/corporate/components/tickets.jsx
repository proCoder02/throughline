// Shared ticket pieces: status/priority chips, keys turned into links, the
// "Tag to ticket" picker, and the tickets shown on a meeting, call or email.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { CloseIcon, TagIcon } from '../../../icons.jsx';

export const TICKET_STATUS = { todo: 'To do', in_progress: 'In progress', in_review: 'In review', done: 'Done', cancelled: 'Cancelled' };
export const PRIORITY = { urgent: 'Urgent', high: 'High', medium: 'Medium', low: 'Low' };
export const ticketPath = (ref) => `/app/corp/tickets/${encodeURIComponent(ref)}`;

export function TicketStatus({ status }) {
  return <span className={'tk-status ' + status}>{TICKET_STATUS[status] || status}</span>;
}

export function Priority({ priority }) {
  if (!priority || priority === 'medium') return null;
  return <span className={'tk-prio ' + priority}>{PRIORITY[priority]}</span>;
}

// "see PAY-12" -> a link to PAY-12 (keys are typed in capitals).
const KEY_RE = /\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b/g;
export function WithTicketLinks({ text }) {
  const parts = String(text || '').split(KEY_RE);
  return parts.map((p, i) => (i % 2 === 1
    ? <Link key={i} className="tk-key-link" to={ticketPath(p)}>{p}</Link>
    : <span key={i}>{p}</span>));
}

/** Pick an open ticket and tag something to it. */
export function TagToTicket({ corp, sourceType, sourceId, onClose, onTagged }) {
  const orgId = corp.data.org.id;
  const [q, setQ] = useState('');
  const [list, setList] = useState(null);
  const [picked, setPicked] = useState(null);
  const [shared, setShared] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef(null);
  useEffect(() => { input.current?.focus(); }, []);
  useEffect(() => {
    const t = setTimeout(() => {
      corpApi.tickets(orgId, { q, status: 'open' }).then((d) => setList(d.tickets)).catch(() => setList([]));
    }, 200);
    return () => clearTimeout(t);
  }, [orgId, q]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const tag = async () => {
    setBusy(true);
    try {
      await corpApi.tagTicket(orgId, picked.id, sourceType, sourceId, shared);
      showToast(`Tagged to ${picked.key}`, 'success');
      onTagged?.(picked);
      onClose();
    } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };
  const what = { message: 'message', email: 'email', meeting: sourceType === 'meeting' ? 'call or meeting' : 'item', commitment: 'commitment' }[sourceType];
  return (
    <div className="confirm-overlay" role="dialog" aria-modal="true" aria-labelledby="tag-title" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="confirm-card tk-tag-card">
        <div className="tk-tag-head">
          <div className="confirm-title" id="tag-title">Tag this {what} to a ticket</div>
          <button className="corp-icon-btn" onClick={onClose} aria-label="Close"><CloseIcon /></button>
        </div>
        <input ref={input} className="tk-search" aria-label="Find a ticket" placeholder="Key or words, e.g. PAY-12 or refund"
          value={q} onChange={(e) => { setQ(e.target.value); setPicked(null); }} />
        <ul className="tk-pick" role="listbox" aria-label="Open tickets">
          {!list ? <li className="corp-muted corp-pad">Loading…</li>
            : list.length === 0 ? <li className="corp-muted corp-pad">No open tickets match.</li>
            : list.slice(0, 30).map((t) => (
              <li key={t.id} role="option" aria-selected={picked?.id === t.id}>
                <button className={'tk-pick-row' + (picked?.id === t.id ? ' on' : '')} onClick={() => setPicked(t)}>
                  <b>{t.key}</b><span className="corp-ellipsis">{t.title}</span><TicketStatus status={t.status} />
                </button>
              </li>))}
        </ul>
        <label className="consent-check tk-share">
          <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
          {' '}Share the full {what} with everyone on the ticket
          <span className="corp-muted corp-small">Otherwise people who weren’t part of it see only a one-line summary.</span>
        </label>
        <div className="confirm-actions">
          <button className="corp-btn" onClick={onClose}>Cancel</button>
          <button className="corp-btn primary" disabled={!picked || busy} onClick={tag}>{picked ? `Tag to ${picked.key}` : 'Tag'}</button>
        </div>
      </div>
    </div>
  );
}

/** Tickets on one meeting, call or email: links, suggestions to confirm, and a Tag button. */
export function TicketsOnItem({ corp, sourceType, sourceId, title = 'Tickets' }) {
  const orgId = corp.data.org.id;
  const [list, setList] = useState(null);
  const [tagging, setTagging] = useState(false);
  const load = useCallback(() => corpApi.ticketsOn(orgId, sourceType, sourceId).then((d) => setList(d.tickets)).catch(() => setList([])),
    [orgId, sourceType, sourceId]);
  useEffect(() => { load(); }, [load]);
  const decide = async (t, action) => {
    try { await corpApi.ticketSuggestion(orgId, t.id, t.link_id, action); load(); showToast(action === 'confirm' ? `Linked to ${t.key}` : 'Dismissed', 'success'); }
    catch (e) { showToast(e.message, 'error'); }
  };
  if (!list) return null;
  const linked = list.filter((t) => t.status === 'linked');
  const suggested = list.filter((t) => t.status === 'suggested');
  return (
    <section className="corp-card tk-on-item">
      <div className="proj-sec-head"><h2>{title}</h2>
        <button className="corp-btn small" onClick={() => setTagging(true)}><TagIcon aria-hidden="true" /> Tag to ticket</button></div>
      {suggested.map((t) => (
        <div key={t.link_id} className="tk-suggest">
          <span>Was this about <Link className="corp-link" to={ticketPath(t.key)}><b>{t.key}</b> {t.title}</Link>?</span>
          <span className="corp-inline">
            <button className="corp-btn small primary" onClick={() => decide(t, 'confirm')}>Yes, link it</button>
            <button className="corp-btn small" onClick={() => decide(t, 'reject')}>No</button>
          </span>
        </div>))}
      {linked.length > 0 ? (
        <div className="corp-chips">{linked.map((t) => (
          <Link key={t.link_id} className="corp-tag" to={ticketPath(t.key)}><b>{t.key}</b>&nbsp;{t.title}</Link>))}</div>
      ) : suggested.length === 0 && <p className="corp-muted corp-small">Not tagged to any ticket.</p>}
      {tagging && <TagToTicket corp={corp} sourceType={sourceType} sourceId={sourceId} onClose={() => setTagging(false)} onTagged={load} />}
    </section>
  );
}

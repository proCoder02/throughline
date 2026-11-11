// One ticket: what it is, who's on it, and the trace -- every chat message,
// email, call and comment about it on one timeline, plus an AI brief of what
// we know so far. Private conversations show as a one-line summary to people
// who weren't part of them (unless whoever tagged them chose to share).
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { fmtDate } from '../components/work.jsx';
import { ChatIcon, MailIcon, MicIcon, SparkleIcon, TaskIcon, TrashIcon } from '../../../icons.jsx';
import { PRIORITY, TICKET_STATUS, TicketStatus, WithTicketLinks } from '../components/tickets.jsx';

const when = (iso) => new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
const HOW = { key: 'mentioned by key', manual: 'tagged', ai: 'suggested, confirmed' };
const ICON = { message: ChatIcon, email: MailIcon, meeting: MicIcon, commitment: TaskIcon };
const OPEN = { thread: (o) => `/app/corp/messages?t=${o.id}`, meeting: (o) => `/app/corp/meetings/${o.id}`, commitment: () => '/app/corp/commitments' };

function sentence(e) {
  const d = e.detail || {};
  const who = e.actor || 'Throughline';
  switch (e.kind) {
    case 'created': return `${who} created this`;
    case 'status': return `${who} moved it from ${TICKET_STATUS[d.from] || d.from} to ${TICKET_STATUS[d.to] || d.to}`;
    case 'priority': return `${who} set priority to ${PRIORITY[d.to] || d.to}`;
    case 'due': return d.to ? `${who} set the due date to ${fmtDate(d.to)}` : `${who} removed the due date`;
    case 'title': return `${who} renamed it`;
    case 'description': return `${who} edited the description`;
    case 'assigned': return `${who} assigned ${e.person || 'someone'}`;
    case 'unassigned': return `${who} unassigned ${e.person || 'someone'}`;
    case 'watching': return `${e.person || 'Someone'} is following`;
    case 'unwatched': return `${e.person || 'Someone'} stopped following`;
    case 'unlinked': return `${who} removed a ${(d.label || d.type || 'link').toLowerCase()}`;
    default: return `${who}: ${e.kind.replace(/_/g, ' ')}`;
  }
}

function LinkItem({ l, canRemove, onRemove }) {
  const [open, setOpen] = useState(false);
  const Icon = ICON[l.type] || ChatIcon;
  const to = l.open && OPEN[l.open.type]?.(l.open);
  return (
    <div className="tk-link">
      <div className="tk-link-head">
        <Icon aria-hidden="true" />
        <span className="tk-link-label">{to ? <Link className="corp-link" to={to}>{l.label}</Link> : l.label}</span>
        <span className="corp-muted corp-small">{l.tagged_by ? `${l.tagged_by} · ` : ''}{HOW[l.how]}{l.shared ? ' · shared' : ''}</span>
        {canRemove && <button className="corp-icon-btn danger" onClick={onRemove} aria-label="Remove from ticket" title="Remove from ticket"><TrashIcon /></button>}
      </div>
      <p className="tk-link-summary">{l.summary || <span className="corp-muted">Summary on its way…</span>}</p>
      {l.content && (
        <>
          <button className="corp-link corp-small" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? 'Hide' : l.type === 'meeting' ? 'Show what was said' : `Show the ${l.type}`}</button>
          {open && <pre className="corp-transcript tk-link-content"><WithTicketLinks text={l.content} /></pre>}
        </>
      )}
      {!l.content && !l.open && <p className="corp-muted corp-small">Private to the people who were in it.</p>}
    </div>
  );
}

export default function TicketPage({ corp, user }) {
  const { ref } = useParams();
  const navigate = useNavigate();
  const orgId = corp.data.org.id;
  const [tid, setTid] = useState(/^\d+$/.test(ref) ? Number(ref) : null);
  const [d, setD] = useState(null);
  const [people, setPeople] = useState([]);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null); // 'title' | 'description'
  const [draft, setDraft] = useState('');

  // A key in the address (/tickets/PAY-12) resolves to the ticket.
  useEffect(() => {
    if (/^\d+$/.test(ref)) { setTid(Number(ref)); return; }
    corpApi.tickets(orgId, { q: ref, status: 'all' }).then((r) => {
      const t = r.tickets.find((x) => x.key === ref.toUpperCase());
      if (t) setTid(t.id); else { showToast(`${ref} not found, or not shared with you.`, 'error'); navigate('/app/corp/tickets'); }
    }).catch(() => navigate('/app/corp/tickets'));
  }, [orgId, ref]); // eslint-disable-line react-hooks/exhaustive-deps
  const load = useCallback(() => tid && corpApi.ticket(orgId, tid).then(setD)
    .catch((e) => { showToast(e.message, 'error'); navigate('/app/corp/tickets'); }), [orgId, tid]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setD(null); load(); }, [load]);
  useEffect(() => { corpApi.members(orgId).then((r) => setPeople(r.members)).catch(() => {}); }, [orgId]);

  if (!d) return <div className="corp-center"><div className="corp-spinner" aria-label="Loading" /></div>;
  const t = d.ticket;
  const edit = d.can_edit;
  const run = async (fn, ok) => {
    setBusy(true);
    try { const r = await fn(); if (r?.ticket) setD(r); else await load(); if (ok) showToast(ok, 'success'); }
    catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };
  const patch = (data) => run(() => corpApi.editTicket(orgId, t.id, data));
  const following = d.people.watchers.some((p) => p.user_id === user.id);
  const stale = !t.summary_at || new Date(t.summary_at) < new Date(t.updated_at);

  const startEdit = (field) => { setEditing(field); setDraft(t[field] || ''); };
  const saveEdit = async () => { await patch({ [editing]: draft }); setEditing(null); };

  return (
    <div className="corp-page wide">
      <header className="corp-page-head"><div className="tk-head">
        <div className="corp-eyebrow"><Link className="corp-link" to="/app/corp/tickets">Tickets</Link> · {t.project_name} · <b>{t.key}</b>
          {t.source === 'jira' && t.external_url && <> · <a className="corp-link" href={t.external_url} target="_blank" rel="noreferrer">Open in Jira</a></>}</div>
        {editing === 'title' ? (
          <div className="corp-inline"><input className="tk-title-input" value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={200} autoFocus aria-label="Title" />
            <button className="corp-btn primary small" onClick={saveEdit} disabled={!draft.trim()}>Save</button>
            <button className="corp-btn small" onClick={() => setEditing(null)}>Cancel</button></div>
        ) : <h1>{t.title}{edit && <button className="corp-link corp-small tk-edit" onClick={() => startEdit('title')}>Edit</button>}</h1>}
      </div>
      <button className="corp-btn small" disabled={busy}
        onClick={() => run(() => corpApi.ticketPerson(orgId, t.id, user.id, 'watcher', !following), following ? 'Stopped following' : 'Following')}>
        {following ? 'Following ✓' : 'Follow'}</button></header>

      <div className="tk-layout">
        <div className="tk-main">
          <section className="corp-card">
            <div className="proj-sec-head"><h2>Description</h2>{edit && editing !== 'description' && <button className="corp-link corp-small" onClick={() => startEdit('description')}>Edit</button>}</div>
            {editing === 'description' ? (
              <div className="corp-form"><textarea rows={5} value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={5000} aria-label="Description" autoFocus />
                <div className="corp-inline"><button className="corp-btn primary small" onClick={saveEdit}>Save</button>
                  <button className="corp-btn small" onClick={() => setEditing(null)}>Cancel</button></div></div>
            ) : <p className="tk-desc">{t.description ? <WithTicketLinks text={t.description} /> : <span className="corp-muted">No description.</span>}</p>}
          </section>

          <section className="corp-card tk-brief">
            <div className="proj-sec-head"><h2><SparkleIcon aria-hidden="true" /> What we know so far</h2>
              <button className="corp-btn small" disabled={busy} onClick={() => run(async () => { await corpApi.ticketBrief(orgId, t.id); }, null)}>
                {busy ? 'Writing…' : t.summary ? 'Refresh' : 'Write it'}</button></div>
            {t.summary ? <><pre className="tk-brief-text">{t.summary}</pre>
              <p className="corp-muted corp-small">Written {when(t.summary_at)}{stale ? ' · there’s newer activity' : ''}. Built from summaries, comments and shared items that everyone on the ticket can see.</p></>
              : <p className="corp-muted">A short brief from everything linked and said here.</p>}
          </section>

          {d.suggestions.length > 0 && (
            <section className="corp-card">
              <h2>Was this about {t.key}?</h2>
              <p className="corp-muted corp-small">Throughline thinks these were about this ticket. You were part of them, so you decide.</p>
              {d.suggestions.map((s) => (
                <div key={s.id} className="tk-suggest">
                  <span><b>{s.label}</b>{s.summary ? ` — ${s.summary}` : ''}</span>
                  <span className="corp-inline">
                    <button className="corp-btn small primary" onClick={() => run(() => corpApi.ticketSuggestion(orgId, t.id, s.id, 'confirm'), 'Linked')}>Yes, link it</button>
                    <button className="corp-btn small" onClick={() => run(() => corpApi.ticketSuggestion(orgId, t.id, s.id, 'reject'), 'Dismissed')}>No</button>
                  </span>
                </div>))}
            </section>
          )}

          <section className="corp-card">
            <h2>Activity</h2>
            <ol className="tk-timeline" aria-label="Activity">
              {d.timeline.map((x, i) => (
                <li key={i} className={'tk-ev ' + x.type}>
                  {x.type === 'comment' ? (
                    <div className="tk-comment"><b>{x.actor}</b> <span className="corp-muted corp-small">{when(x.at)}</span>
                      <p><WithTicketLinks text={x.body} /></p></div>
                  ) : x.type === 'link' ? (
                    <><span className="corp-muted corp-small">{when(x.at)}</span>
                      <LinkItem l={{ ...x, type: x.source_type }} canRemove={edit || x.tagged_by_id === user.id}
                        onRemove={() => run(() => corpApi.untag(orgId, t.id, x.id), 'Removed')} /></>
                  ) : <span className="tk-ev-text">{sentence(x)} <span className="corp-muted corp-small">· {when(x.at)}</span></span>}
                </li>))}
            </ol>
            <form className="corp-composer tk-composer" onSubmit={async (e) => {
              e.preventDefault();
              if (!comment.trim()) return;
              await run(() => corpApi.ticketComment(orgId, t.id, comment));
              setComment('');
            }}>
              <input aria-label="Comment" placeholder="Add a comment" value={comment} onChange={(e) => setComment(e.target.value)} maxLength={5000} />
              <button className="corp-btn primary" disabled={busy || !comment.trim()}>Comment</button>
            </form>
          </section>
        </div>

        <aside className="tk-side">
          <section className="corp-card corp-form">
            <label className="corp-field"><span>Status</span>
              <select value={t.status} disabled={!edit || busy} onChange={(e) => patch({ status: e.target.value })}>
                {Object.entries(TICKET_STATUS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="corp-field"><span>Priority</span>
              <select value={t.priority} disabled={!edit || busy} onChange={(e) => patch({ priority: e.target.value })}>
                {Object.entries(PRIORITY).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="corp-field"><span>Due</span>
              <input type="date" value={t.due_date || ''} disabled={!edit || busy} onChange={(e) => patch({ due_date: e.target.value || null })} /></label>
            <div className="corp-field"><span>Assignees</span>
              <div className="corp-chips">
                {d.people.assignees.map((p) => (
                  <span key={p.user_id} className="corp-chip on">{p.username}
                    {edit && <button className="corp-chip-x" aria-label={`Unassign ${p.username}`}
                      onClick={() => run(() => corpApi.ticketPerson(orgId, t.id, p.user_id, 'assignee', false))}>×</button>}</span>))}
                {d.people.assignees.length === 0 && <span className="corp-muted corp-small">Nobody yet</span>}
              </div>
              {edit && (
                <select aria-label="Assign someone" value="" onChange={(e) => e.target.value && run(() => corpApi.ticketPerson(orgId, t.id, Number(e.target.value), 'assignee', true))}>
                  <option value="">Assign someone…</option>
                  {people.filter((p) => !d.people.assignees.some((a) => a.user_id === p.user_id)).map((p) => <option key={p.user_id} value={p.user_id}>{p.username}</option>)}
                </select>)}
            </div>
            <label className="corp-field"><span>Supports</span>
              <select value={d.supports?.id || ''} disabled={!edit || busy} onChange={(e) => patch({ supports_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">No client deliverable</option>
                {d.supports && !d.deliverables.some((x) => x.id === d.supports.id) && <option value={d.supports.id}>{d.supports.text}</option>}
                {d.deliverables.map((x) => <option key={x.id} value={x.id}>{x.text}{x.due_date ? ` (due ${fmtDate(x.due_date)})` : ''}</option>)}
              </select></label>
            {d.supports && <p className="corp-muted corp-small">Progress and delays on this ticket show on that client promise.</p>}
            {d.people.watchers.length > 0 && <div className="corp-field"><span>Following</span>
              <span className="corp-small">{d.people.watchers.map((p) => p.username).join(', ')}</span></div>}
            <p className="corp-muted corp-small">Tag chat, email or calls from where they happen, or type <b>{t.key}</b> in a message or email subject.</p>
          </section>
        </aside>
      </div>
    </div>
  );
}

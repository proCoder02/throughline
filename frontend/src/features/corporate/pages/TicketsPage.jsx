// Tickets: small issues inside projects (PAY-12), assigned to people, with
// everything said about them in chat, email and calls traced on each ticket.
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { fmtDate } from '../components/work.jsx';
import { PRIORITY, Priority, TicketStatus, ticketPath } from '../components/tickets.jsx';

export function PeoplePicker({ people, value, onChange, label = 'Assignees' }) {
  const chosen = people.filter((p) => value.includes(p.user_id));
  return (
    <div className="corp-field compact tk-people-field"><span>{label}</span>
      <div className="corp-chips">
        {chosen.map((p) => (
          <button type="button" key={p.user_id} className="corp-chip on" onClick={() => onChange(value.filter((v) => v !== p.user_id))}
            aria-label={`Remove ${p.username}`}>{p.username} ×</button>))}
        <select aria-label={`Add to ${label.toLowerCase()}`} value="" onChange={(e) => e.target.value && onChange([...value, Number(e.target.value)])}>
          <option value="">Add…</option>
          {people.filter((p) => !value.includes(p.user_id)).map((p) => <option key={p.user_id} value={p.user_id}>{p.username}</option>)}
        </select>
      </div>
    </div>
  );
}

function NewTicket({ corp, projects, onCreated, onCancel }) {
  const orgId = corp.data.org.id;
  const keyed = projects.filter((p) => p.ticket_key);
  const [f, setF] = useState({ project_id: keyed[0]?.id ?? '', title: '', description: '', priority: 'medium', due_date: '', assignees: [] });
  const [people, setPeople] = useState([]);
  const [deliverables, setDeliverables] = useState([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { corpApi.members(orgId).then((d) => setPeople(d.members)).catch(() => {}); }, [orgId]);
  useEffect(() => {
    if (!f.project_id) return;
    corpApi.project(orgId, f.project_id).then((d) => setDeliverables(d.deliverables.filter((x) => !x.supports_id
      && !['done', 'dropped', 'declined'].includes(x.status)))).catch(() => setDeliverables([]));
  }, [orgId, f.project_id]);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  if (!keyed.length) {
    return (
      <section className="corp-card corp-warn">
        None of your projects has a ticket key yet. A project lead or admin sets one (e.g. PAY) on the project’s page.
        <div><button className="corp-btn small" onClick={onCancel}>OK</button></div>
      </section>
    );
  }
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const d = await corpApi.createTicket(orgId, { ...f, project_id: Number(f.project_id), due_date: f.due_date || null,
        supports_id: f.supports_id ? Number(f.supports_id) : null });
      onCreated(d.ticket);
    } catch (err) { showToast(err.message, 'error'); } finally { setBusy(false); }
  };
  return (
    <form className="corp-card corp-form" onSubmit={save}>
      <h2>New ticket</h2>
      <div className="corp-inline wrap">
        <label className="corp-field compact"><span>Project</span>
          <select value={f.project_id} onChange={set('project_id')}>
            {keyed.map((p) => <option key={p.id} value={p.id}>{p.ticket_key} · {p.name}</option>)}
          </select></label>
        <label className="corp-field compact"><span>Priority</span>
          <select value={f.priority} onChange={set('priority')}>{Object.entries(PRIORITY).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label className="corp-field compact"><span>Due</span><input type="date" value={f.due_date} onChange={set('due_date')} /></label>
      </div>
      <label className="corp-field"><span>Title</span>
        <input value={f.title} onChange={set('title')} maxLength={200} placeholder="e.g. Refund webhook times out" autoFocus /></label>
      <label className="corp-field"><span>Description</span>
        <textarea rows={3} value={f.description} onChange={set('description')} maxLength={5000} placeholder="What’s wrong, where, and what done looks like" /></label>
      {deliverables.length > 0 && (
        <label className="corp-field"><span>Supports a client deliverable (optional)</span>
          <select value={f.supports_id || ''} onChange={set('supports_id')}>
            <option value="">None</option>
            {deliverables.map((x) => <option key={x.id} value={x.id}>{x.text}{x.due_date ? ` (due ${fmtDate(x.due_date)})` : ''}</option>)}
          </select></label>)}
      <PeoplePicker people={people} value={f.assignees} onChange={(v) => setF({ ...f, assignees: v })} />
      <div className="corp-inline">
        <button className="corp-btn primary" disabled={busy || !f.title.trim()}>{busy ? 'Creating…' : 'Create ticket'}</button>
        <button type="button" className="corp-btn" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export function TicketRow({ t }) {
  return (
    <li><Link className="corp-row tk-row" to={ticketPath(t.key)}>
      <span className="tk-row-key">{t.key}</span>
      <span className="corp-row-main">
        <span className="corp-row-title">{t.title}</span>
        <span className="corp-row-sub">{[t.project_name, t.assignees || 'Unassigned', t.due_date && `due ${fmtDate(t.due_date)}`,
          t.links > 0 && `${t.links} linked`].filter(Boolean).join(' · ')}</span>
      </span>
      <span className="tk-row-tags"><Priority priority={t.priority} /><TicketStatus status={t.status} /></span>
    </Link></li>
  );
}

export default function TicketsPage({ corp }) {
  const orgId = corp.data.org.id;
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const scope = params.get('scope') || 'mine';
  const status = params.get('status') || 'open';
  const projectId = params.get('project') || '';
  const [q, setQ] = useState('');
  const [list, setList] = useState(null);
  const [projects, setProjects] = useState([]);
  const [creating, setCreating] = useState(false);
  const setParam = (k, v) => { const p = new URLSearchParams(params); if (v) p.set(k, v); else p.delete(k); setParams(p); };
  useEffect(() => { corpApi.projects(orgId).then((d) => setProjects(d.projects)).catch(() => {}); }, [orgId]);
  const load = useCallback(() => {
    corpApi.tickets(orgId, { scope, status, project_id: projectId, q: q.trim() })
      .then((d) => setList(d.tickets)).catch(() => setList([]));
  }, [orgId, scope, status, projectId, q]);
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, q]);
  const tabs = [['mine', 'Assigned to me'], ['watching', 'Following'], ['all', 'All']];
  return (
    <div className="corp-page">
      <header className="corp-page-head"><div><h1>Tickets</h1>
        <p className="corp-muted">Small issues inside projects. Type a key like PAY-12 in chat or an email subject, or tag a call, and it shows up on the ticket.</p></div>
        {!creating && <button className="corp-btn primary" onClick={() => setCreating(true)}>New ticket</button>}</header>
      {creating && <NewTicket corp={corp} projects={projects} onCancel={() => setCreating(false)}
        onCreated={(t) => { setCreating(false); navigate(ticketPath(t.key)); }} />}
      <section className="corp-card">
        <div className="corp-inline wrap tk-filters">
          <div className="corp-seg" role="group" aria-label="Show">
            {tabs.map(([k, l]) => <button key={k} className={scope === k ? 'active' : ''} aria-pressed={scope === k} onClick={() => setParam('scope', k === 'mine' ? '' : k)}>{l}</button>)}
          </div>
          <select aria-label="Project" value={projectId} onChange={(e) => setParam('project', e.target.value)}>
            <option value="">All projects</option>
            {projects.filter((p) => p.ticket_key).map((p) => <option key={p.id} value={p.id}>{p.ticket_key} · {p.name}</option>)}
          </select>
          <select aria-label="Status" value={status} onChange={(e) => setParam('status', e.target.value === 'open' ? '' : e.target.value)}>
            <option value="open">Open</option><option value="all">All</option><option value="done">Done</option><option value="cancelled">Cancelled</option>
          </select>
          <input className="tk-search" aria-label="Search tickets" placeholder="Search key or words" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {!list ? <div className="corp-spinner small" aria-label="Loading" />
          : list.length === 0 ? <p className="corp-muted">{scope === 'mine' ? 'Nothing assigned to you.' : 'No tickets here.'}</p>
          : <ul className="corp-list">{list.map((t) => <TicketRow key={t.id} t={t} />)}</ul>}
      </section>
    </div>
  );
}

// Project dashboard: health at a glance, the stakeholder map (client side by
// organisation and reporting line; your side), and the deliverables -- each
// client promise with the work from other teams it depends on. Pick a person
// on the map to see only what involves them.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { confirmDialog, showToast } from '../../../lib/notify.js';
import { CommitmentRow, StatusChip, fmtDate } from '../components/work.jsx';
import { AskTeam } from '../components/Requests.jsx';
import { HEALTH } from './ProjectsPage.jsx';
import CatchUp from '../components/CatchUp.jsx';
import { TicketProgress } from '../components/tickets.jsx';

// A status update for the client, drafted from the deliverables; the person edits and sends it.
function ClientUpdate({ orgId, p, contacts }) {
  const clientSide = contacts.filter((c) => c.side === 'client');
  const [contactId, setContactId] = useState('');
  const [busy, setBusy] = useState(false);
  const [d, setD] = useState(null);
  const draft = async () => {
    setBusy(true);
    try { setD(await corpApi.clientUpdate(orgId, p.id, contactId ? Number(contactId) : null)); }
    catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };
  const copy = async () => { try { await navigator.clipboard.writeText(`${d.subject}\n\n${d.body}`); showToast('Copied', 'success'); } catch { showToast('Your browser blocked copying.', 'error'); } };
  const mailto = d && `mailto:${encodeURIComponent(d.to || '')}?subject=${encodeURIComponent(d.subject)}&body=${encodeURIComponent(d.body)}`;
  return (
    <section className="corp-card corp-form">
      <div className="proj-sec-head"><h2>Client update</h2></div>
      <p className="corp-muted corp-small">A status note for {p.client || 'the client'} in client-safe words, drafted from the deliverables and the tickets behind them. You edit and send it; nothing goes out by itself.</p>
      <div className="corp-inline wrap">
        <select aria-label="For whom" value={contactId} onChange={(e) => { setContactId(e.target.value); setD(null); }}>
          <option value="">Everything for {p.client || 'the client'}</option>
          {clientSide.map((c) => <option key={c.id} value={c.id}>What was promised to {c.name}</option>)}
        </select>
        <button className="corp-btn primary" onClick={draft} disabled={busy}>{busy ? 'Drafting…' : d ? 'Draft again' : 'Draft update'}</button>
      </div>
      {d && (<>
        {d.internal.length > 0 && <div className="corp-warn tk-internal"><b>Before you send (internal):</b><ul>{d.internal.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
        <label className="corp-field"><span>Subject</span><input value={d.subject} onChange={(e) => setD({ ...d, subject: e.target.value })} /></label>
        <label className="corp-field"><span>Message</span><textarea rows={9} value={d.body} onChange={(e) => setD({ ...d, body: e.target.value })} /></label>
        <div className="corp-inline"><button className="corp-btn" onClick={copy}>Copy</button>
          <a className="corp-btn primary" href={mailto}>Open in email{d.to ? ` to ${d.to}` : ''}</a></div>
      </>)}
    </section>
  );
}

// Ticket key (e.g. PAY): set once by a lead or admin; fixed once tickets exist.
function TicketKey({ orgId, p, open, onSaved }) {
  const [key, setKey] = useState(p.ticket_key || '');
  const [editing, setEditing] = useState(false);
  const save = async (e) => {
    e.preventDefault();
    try { await corpApi.setTicketKey(orgId, p.id, key); setEditing(false); onSaved(); } catch (err) { showToast(err.message, 'error'); }
  };
  return (
    <section className="corp-card">
      <div className="proj-sec-head"><h2>Tickets</h2>
        {p.ticket_key && <Link className="corp-btn small" to={`/app/corp/tickets?project=${p.id}&scope=all`}>{open} open · view all</Link>}</div>
      {p.ticket_key && !editing ? (
        <p className="corp-muted">Tickets in this project are numbered <b>{p.ticket_key}-1</b>, <b>{p.ticket_key}-2</b>… Type a key in chat or an email subject to link it.
          {p.can_edit && open === 0 && <> <button className="corp-link" onClick={() => setEditing(true)}>Change key</button></>}</p>
      ) : p.can_edit ? (
        <form className="corp-inline wrap" onSubmit={save}>
          <label className="corp-field compact"><span>Ticket key</span>
            <input value={key} onChange={(e) => setKey(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 10))} placeholder="e.g. PAY" aria-describedby="key-help" /></label>
          <button className="corp-btn primary small" disabled={key.length < 2}>Save</button>
          <span id="key-help" className="corp-muted corp-small">2–10 capital letters or digits. Can’t change once tickets exist.</span>
        </form>
      ) : <p className="corp-muted">A project lead or admin sets a ticket key (e.g. PAY) to start using tickets here.</p>}
    </section>
  );
}

// Reporting tree from flat contacts; a broken or circular chain becomes a root.
function trees(contacts) {
  const byId = new Map(contacts.map((c) => [c.id, c]));
  const kids = new Map();
  const roots = [];
  for (const c of contacts) {
    let p = c.reports_to, seen = new Set([c.id]), ok = !!p;
    while (p && ok) { if (seen.has(p) || !byId.has(p)) ok = false; else { seen.add(p); p = byId.get(p).reports_to; } }
    if (c.reports_to && ok) { if (!kids.has(c.reports_to)) kids.set(c.reports_to, []); kids.get(c.reports_to).push(c); }
    else roots.push(c);
  }
  return { roots, kids };
}

function Node({ c, kids, counts, selected, onSelect }) {
  const children = kids.get(c.id) || [];
  return (
    <li>
      <button className={'stk-card ' + c.side + (selected === c.id ? ' sel' : '')} onClick={() => onSelect(selected === c.id ? null : c.id)}
        aria-pressed={selected === c.id}>
        <span className="stk-name">{c.name}</span>
        <span className="stk-pos">{c.position || '—'}</span>
        {c.department && <span className="stk-dept">{c.department}</span>}
        {counts[c.id] > 0 && <span className="stk-count" title="Deliverables involving them">{counts[c.id]}</span>}
      </button>
      {children.length > 0 && <ul>{children.map((k) => <Node key={k.id} c={k} kids={kids} counts={counts} selected={selected} onSelect={onSelect} />)}</ul>}
    </li>
  );
}

function ContactForm({ contacts, initial, onSave, onCancel }) {
  const [f, setF] = useState(initial || { side: 'client', organisation: '', department: '', name: '', position: '', reports_to: '', email: '' });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <form className="corp-form stk-form" onSubmit={(e) => { e.preventDefault(); onSave({ ...f, reports_to: f.reports_to ? Number(f.reports_to) : null }); }}>
      <div className="corp-inline wrap">
        <label className="corp-field compact"><span>Side</span>
          <select value={f.side} onChange={set('side')}><option value="client">Client</option><option value="vendor">Vendor (us)</option><option value="partner">Partner</option></select></label>
        <label className="corp-field compact" style={{ flex: 1 }}><span>Name</span><input value={f.name} onChange={set('name')} required maxLength={200} /></label>
        <label className="corp-field compact" style={{ flex: 1 }}><span>Position</span><input value={f.position} onChange={set('position')} placeholder="e.g. Joint Secretary" maxLength={200} /></label>
      </div>
      <div className="corp-inline wrap">
        <label className="corp-field compact" style={{ flex: 1 }}><span>Organisation</span><input value={f.organisation} onChange={set('organisation')} placeholder="e.g. Ministry of IT" maxLength={200} /></label>
        <label className="corp-field compact" style={{ flex: 1 }}><span>Department</span><input value={f.department} onChange={set('department')} placeholder="e.g. e-Governance" maxLength={200} /></label>
        <label className="corp-field compact"><span>Reports to</span>
          <select value={f.reports_to || ''} onChange={set('reports_to')}><option value="">No one / top</option>
            {contacts.filter((c) => c.id !== initial?.id).map((c) => <option key={c.id} value={c.id}>{c.name}{c.position ? ` (${c.position})` : ''}</option>)}</select></label>
      </div>
      <div className="corp-actions"><button type="button" className="corp-btn" onClick={onCancel}>Cancel</button><button className="corp-btn primary">Save</button></div>
    </form>
  );
}

export default function ProjectPage({ corp }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const orgId = corp.data.org.id;
  const [d, setD] = useState(null);
  const [selected, setSelected] = useState(null);
  const [editing, setEditing] = useState(null); // 'new' | contact
  const [panel, setPanel] = useState(null); // 'promise' | 'ask' | 'team'
  const [promise, setPromise] = useState({ text: '', due_date: '', contact_id: '' });
  const [people, setPeople] = useState([]);
  const load = useCallback(() => corpApi.project(orgId, id).then(setD).catch((e) => { showToast(e.message, 'error'); navigate('/app/corp/projects'); }), [orgId, id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [load]);
  useEffect(() => { corpApi.members(orgId).then((r) => setPeople(r.members)).catch(() => {}); }, [orgId]);

  const counts = useMemo(() => {
    const out = {};
    (d?.deliverables || []).forEach((x) => { if (x.contact_id) out[x.contact_id] = (out[x.contact_id] || 0) + 1; });
    return out;
  }, [d]);
  if (!d) return <div className="corp-center"><div className="corp-spinner" aria-label="Loading" /></div>;
  const { project: p, contacts, deliverables, members, health, counts: n } = d;
  const [hLabel, hTone] = HEALTH[health];
  const sides = ['client', 'vendor', 'partner'].map((s) => ({ s, list: contacts.filter((c) => c.side === s) })).filter((x) => x.list.length);
  const promises = deliverables.filter((x) => !x.supports_id || !deliverables.some((y) => y.id === x.supports_id));
  const shown = selected ? promises.filter((x) => x.contact_id === selected) : promises;
  const childrenOf = (pid) => deliverables.filter((x) => x.supports_id === pid);

  const saveContact = async (data) => {
    try { setD(editing === 'new' ? await corpApi.addContact(orgId, id, data) : await corpApi.editContact(orgId, id, editing.id, data)); setEditing(null); }
    catch (e) { showToast(e.message, 'error'); }
  };
  const removeContact = async (c) => {
    if (!(await confirmDialog({ title: `Remove ${c.name}?`, confirmLabel: 'Remove', message: 'People who reported to them move to the top level.' }))) return;
    try { setD(await corpApi.deleteContact(orgId, id, c.id)); setEditing(null); setSelected(null); } catch (e) { showToast(e.message, 'error'); }
  };
  const addPromise = async (e) => {
    e.preventDefault();
    try {
      await corpApi.addCommitment(orgId, { text: promise.text, due_date: promise.due_date || null, project_id: Number(id),
        contact_id: promise.contact_id ? Number(promise.contact_id) : null });
      setPromise({ text: '', due_date: '', contact_id: '' }); setPanel(null); load();
    } catch (err) { showToast(err.message, 'error'); }
  };
  const setMember = async (userId, role) => { try { setD(await corpApi.projectMember(orgId, id, userId, role)); } catch (e) { showToast(e.message, 'error'); } };
  const sel = contacts.find((c) => c.id === selected);

  return (
    <div className="corp-page wide">
      <header className="corp-page-head"><div>
        <div className="corp-eyebrow"><Link className="corp-link" to="/app/corp/projects">Projects</Link>{p.client ? ` · ${p.client}` : ''}</div>
        <h1>{p.name}</h1>
        <p className="corp-muted">{[p.start_date && `from ${fmtDate(p.start_date)}`, p.end_date && `ends ${fmtDate(p.end_date)}`, `${members.length} on the team`].filter(Boolean).join(' · ')}</p>
      </div><span className={'proj-health big ' + hTone}>{hLabel}</span></header>

      <section className="corp-card"><CatchUp corp={corp} label="Project brief" periods={false} params={{ project_id: Number(id) }} /></section>

      <TicketKey orgId={orgId} p={p} open={n.tickets_open ?? 0} onSaved={load} />
      <ClientUpdate orgId={orgId} p={p} contacts={contacts} />

      <div className="proj-kpis">
        <div><b>{n.open}</b><span>open</span></div>
        <div className={n.at_risk ? 'warn' : ''}><b>{n.at_risk}</b><span>at risk</span></div>
        <div className={n.late ? 'crit' : ''}><b>{n.late}</b><span>late</span></div>
        <div><b>{n.done}</b><span>done</span></div>
      </div>

      <section className="corp-card">
        <div className="proj-sec-head"><h2>Stakeholders</h2>
          <button className="corp-btn small" onClick={() => setEditing('new')}>Add person</button></div>
        {editing === 'new' && <ContactForm contacts={contacts} onSave={saveContact} onCancel={() => setEditing(null)} />}
        {contacts.length === 0 ? <p className="corp-muted">Add the client’s officials (e.g. Secretary → Joint Secretary → Director) and your side, with who reports to whom.</p> : (
          <div className="stk-sides">{sides.map(({ s, list }) => {
            const { roots, kids } = trees(list);
            const orgs = [...new Set(roots.map((r) => r.organisation || (s === 'vendor' ? 'Us' : 'Unspecified')))];
            return (
              <div key={s} className="stk-side">
                <h3>{s === 'client' ? 'Client' : s === 'vendor' ? 'Our side' : 'Partners'}</h3>
                {orgs.map((o) => (
                  <div key={o} className="stk-org"><div className="stk-org-name">{o}</div>
                    <ul className="stk-tree">{roots.filter((r) => (r.organisation || (s === 'vendor' ? 'Us' : 'Unspecified')) === o).map((r) => (
                      <Node key={r.id} c={r} kids={kids} counts={counts} selected={selected} onSelect={setSelected} />))}</ul>
                  </div>))}
              </div>);
          })}</div>
        )}
        {sel && editing !== 'new' && (
          <div className="stk-detail">
            {editing?.id === sel.id ? <ContactForm contacts={contacts} initial={{ ...sel, reports_to: sel.reports_to || '' }} onSave={saveContact} onCancel={() => setEditing(null)} /> : (
              <>
                <span><strong>{sel.name}</strong> · {sel.position}{sel.department ? `, ${sel.department}` : ''}{sel.organisation ? ` · ${sel.organisation}` : ''}
                  {sel.email ? ` · ${sel.email}` : ''}</span>
                <span className="corp-inline"><CatchUp key={sel.id} corp={corp} label={`Prepare for meeting with ${sel.name}`} periods={false}
                  params={{ project_id: Number(id), contact_id: sel.id }} />
                  <button className="corp-btn small" onClick={() => setEditing(sel)}>Edit</button>
                  <button className="corp-btn small danger" onClick={() => removeContact(sel)}>Remove</button></span>
              </>)}
          </div>
        )}
      </section>

      <section className="corp-card">
        <div className="proj-sec-head"><h2>Deliverables{selected ? ` involving ${sel?.name}` : ''}</h2>
          <span className="corp-inline">
            {selected && <button className="corp-btn small" onClick={() => setSelected(null)}>Show all</button>}
            <button className="corp-btn small" onClick={() => setPanel(panel === 'promise' ? null : 'promise')}>Add promise</button>
            <button className="corp-btn small" onClick={() => setPanel(panel === 'ask' ? null : 'ask')}>Ask a team</button>
          </span></div>
        {panel === 'promise' && (
          <form className="corp-inline wrap proj-panel" onSubmit={addPromise}>
            <input aria-label="Promise" style={{ flex: 2 }} placeholder="What you promised the client" value={promise.text} onChange={(e) => setPromise({ ...promise, text: e.target.value })} />
            <select aria-label="Client counterpart" value={promise.contact_id} onChange={(e) => setPromise({ ...promise, contact_id: e.target.value })}>
              <option value="">To whom (optional)</option>{contacts.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
            <input type="date" aria-label="Due" value={promise.due_date} onChange={(e) => setPromise({ ...promise, due_date: e.target.value })} />
            <button className="corp-btn primary" disabled={!promise.text.trim()}>Add</button>
          </form>)}
        {panel === 'ask' && <div className="proj-panel"><AskTeam corp={corp} projectId={Number(id)}
          supportsOptions={promises.filter((x) => ['open', 'overdue', 'detected'].includes(x.status))} onSent={() => { setPanel(null); load(); }} /></div>}
        {shown.length === 0 ? <p className="corp-muted">{selected ? 'Nothing involves them yet.' : 'Add what you’ve promised the client, then ask the teams who’ll deliver it.'}</p> : (
          <ul className="corp-list">{shown.map((x) => (
            <li key={x.id} className="proj-deliv">
              <ul className="corp-list"><CommitmentRow corp={corp} c={x} onChanged={load} /></ul>
              {x.tickets && <TicketProgress t={x.tickets} />}
              {childrenOf(x.id).length > 0 && (
                <ul className="proj-deps">{childrenOf(x.id).map((k) => (
                  <li key={k.id}>
                    <span className="corp-row-main"><span className="corp-row-title">{k.unit_name}: {k.text}</span>
                      <span className="corp-row-sub">{k.owner || 'awaiting owner'} · {k.due_date ? `due ${fmtDate(k.due_date)}` : 'no date'}
                        {k.requested_due && k.due_date && k.due_date > k.requested_due ? ` (asked for ${fmtDate(k.requested_due)})` : ''}</span>
                      {k.blocker && <span className="upd-blocker">Blocked: {k.blocker}</span>}</span>
                    <StatusChip status={k.status} />
                  </li>))}
                </ul>)}
            </li>))}
          </ul>)}
      </section>

      <section className="corp-card">
        <div className="proj-sec-head"><h2>Project team</h2>
          {p.can_edit && (
            <select aria-label="Add to project" value="" onChange={(e) => e.target.value && setMember(Number(e.target.value), 'member')}>
              <option value="">Add someone…</option>
              {people.filter((x) => !members.some((m) => m.user_id === x.user_id)).map((x) => <option key={x.user_id} value={x.user_id}>{x.username}</option>)}
            </select>)}</div>
        <ul className="corp-list">{members.map((m) => (
          <li key={m.user_id} className="corp-person"><span className="corp-row-main"><span className="corp-row-title">{m.username}</span>
            <span className="corp-row-sub">{m.title || ''}</span></span>
            {p.can_edit ? (
              <span className="corp-inline">
                <select aria-label={`Role of ${m.username}`} value={m.role} onChange={(e) => setMember(m.user_id, e.target.value)}>
                  <option value="lead">Lead</option><option value="member">Member</option>
                  <option value="stakeholder">Stakeholder (view only)</option></select>
                <button className="corp-btn small" onClick={() => setMember(m.user_id, null)}>Remove</button></span>
            ) : <span className="corp-role">{{ lead: 'Lead', member: 'Member', stakeholder: 'Stakeholder' }[m.role] || m.role}</span>}
          </li>))}</ul>
      </section>
    </div>
  );
}

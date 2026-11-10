// Cross-team requests: "Ask a team" (e.g. Sales asks Engineering, linked to
// the client promise it supports) and the incoming list a team lead answers.
import { useCallback, useEffect, useState } from 'react';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { fmtDate } from './work.jsx';

export function AskTeam({ corp, projectId = null, supportsOptions = null, onSent }) {
  const orgId = corp.data.org.id;
  const [text, setText] = useState('');
  const [unitId, setUnitId] = useState('');
  const [due, setDue] = useState('');
  const [supports, setSupports] = useState('');
  const [mine, setMine] = useState(supportsOptions || []);
  useEffect(() => {
    if (supportsOptions) { setMine(supportsOptions); return; }
    corpApi.commitments(orgId, 'mine').then((d) => setMine(d.commitments.filter((c) => ['open', 'overdue', 'detected'].includes(c.status)))).catch(() => {});
  }, [orgId, supportsOptions]);
  const send = async (e) => {
    e.preventDefault();
    try {
      await corpApi.request(orgId, { text, unit_id: Number(unitId), due_date: due, supports_id: supports ? Number(supports) : null,
        project_id: projectId });
      setText(''); setDue(''); setSupports('');
      showToast('Request sent. Their lead will accept it with an owner and a date.', 'success');
      onSent?.();
    } catch (err) { showToast(err.message, 'error'); }
  };
  return (
    <form className="corp-form" onSubmit={send}>
      <input aria-label="What you need" placeholder="What do you need from another team?" value={text} onChange={(e) => setText(e.target.value)} maxLength={500} />
      <div className="corp-inline wrap">
        <label className="corp-field compact"><span>Team</span>
          <select value={unitId} onChange={(e) => setUnitId(e.target.value)} required>
            <option value="">Choose…</option>
            {corp.data.units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select></label>
        <label className="corp-field compact"><span>Needed by</span><input type="date" value={due} onChange={(e) => setDue(e.target.value)} required /></label>
        <label className="corp-field compact" style={{ flex: 1 }}><span>For (your promise it supports)</span>
          <select value={supports} onChange={(e) => setSupports(e.target.value)}>
            <option value="">Nothing specific</option>
            {mine.map((c) => <option key={c.id} value={c.id}>{c.text}{c.due_date ? ` · due ${fmtDate(c.due_date)}` : ''}</option>)}
          </select></label>
        <button className="corp-btn primary" disabled={!text.trim() || !unitId || !due}>Send request</button>
      </div>
    </form>
  );
}

function Incoming({ corp, r, onDone }) {
  const orgId = corp.data.org.id;
  const [people, setPeople] = useState([]);
  const [owner, setOwner] = useState('');
  const [due, setDue] = useState(r.requested_due || '');
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState('');
  useEffect(() => { corpApi.unitMembers(orgId, r.unit_id).then((d) => setPeople(d.members)).catch(() => {}); }, [orgId, r.unit_id]);
  const act = async (fn) => { try { await fn(); onDone(); } catch (e) { showToast(e.message, 'error'); } };
  return (
    <li className="req">
      <div className="corp-row-title">{r.text}</div>
      <div className="corp-row-sub">
        For {r.unit_name} · asked by {r.requested_by_name} · needed by {fmtDate(r.requested_due)}
        {r.supports_text ? ` · supports “${r.supports_text}”` : ''}{r.project_name ? ` · ${r.project_name}` : ''}
      </div>
      {declining ? (
        <div className="corp-inline wrap">
          <input aria-label="Reason" placeholder="Why, so they can plan around it" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="corp-btn small danger" disabled={!reason.trim()} onClick={() => act(() => corpApi.declineRequest(orgId, r.id, reason))}>Decline</button>
          <button className="corp-btn small" onClick={() => setDeclining(false)}>Cancel</button>
        </div>
      ) : (
        <div className="corp-inline wrap">
          <select aria-label="Owner" value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="">Owner: me</option>
            {people.map((p) => <option key={p.user_id} value={p.user_id}>{p.username}</option>)}
          </select>
          <input type="date" aria-label="Date you can commit to" value={due} onChange={(e) => setDue(e.target.value)} />
          {due && r.requested_due && due > r.requested_due && <span className="corp-warn corp-small">Later than asked; they'll be told</span>}
          <button className="corp-btn small primary" onClick={() => act(() => corpApi.acceptRequest(orgId, r.id, { owner_id: owner ? Number(owner) : null, due_date: due }))}>Accept</button>
          <button className="corp-btn small" onClick={() => setDeclining(true)}>Decline…</button>
        </div>
      )}
    </li>
  );
}

export function IncomingRequests({ corp, onChanged }) {
  const [list, setList] = useState(null);
  const load = useCallback(() => corpApi.incoming(corp.data.org.id).then((d) => setList(d.requests)).catch(() => setList([])), [corp.data.org.id]);
  useEffect(() => { load(); }, [load]);
  if (!list || !list.length) return null;
  return (
    <section className="corp-card">
      <h2>Requests for your teams ({list.length})</h2>
      <ul className="corp-list">{list.map((r) => <Incoming key={r.id} corp={corp} r={r} onDone={() => { load(); onChanged?.(); }} />)}</ul>
    </section>
  );
}

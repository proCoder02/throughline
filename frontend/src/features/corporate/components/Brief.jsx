// Daily brief (what you owe, what you're owed, what's at risk) and the
// opt-in work note used by common ground.
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { CommitmentRow, fmtDate } from './work.jsx';

export function Brief({ corp }) {
  const [b, setB] = useState(null);
  const load = useCallback(() => corpApi.brief(corp.data.org.id).then(setB).catch(() => setB(null)), [corp.data.org.id]);
  useEffect(() => { load(); }, [load]);
  if (!b) return null;
  const empty = !b.owe.length && !b.owed.length && !b.risks.length && !b.decisions.length;
  return (
    <section className="corp-card">
      <h2>Your brief</h2>
      {empty && <p className="corp-muted">Nothing open. Add a meeting or a commitment to get started.</p>}
      {b.risks.length > 0 && (
        <div className="corp-brief-block risk"><h3>At risk ({b.risks.length})</h3><ul className="corp-list">{b.risks.map((r) => (
          <li key={r.id} className="corp-person"><span className="corp-row-main">
            <span className="corp-row-title">{r.commitment || `A ${r.commitment_unit} commitment`}{r.due_date ? ` · due ${fmtDate(r.due_date)}` : ''}</span>
            <span className="corp-row-sub">{r.blocker ? `Your team's blocker: ${r.blocker}` : `A blocker was logged in ${r.blocker_unit}`}</span>
          </span></li>))}</ul></div>
      )}
      {b.owe.length > 0 && <div className="corp-brief-block"><h3>You owe ({b.owe.length})</h3>
        <ul className="corp-list">{b.owe.slice(0, 6).map((c) => <CommitmentRow key={c.id} corp={corp} c={{ ...c, can_change: true }} onChanged={load} />)}</ul></div>}
      {b.owed.length > 0 && <div className="corp-brief-block"><h3>Owed to you ({b.owed.length})</h3>
        <ul className="corp-list">{b.owed.slice(0, 6).map((c) => <CommitmentRow key={c.id} corp={corp} c={{ ...c, can_change: true }} onChanged={load} />)}</ul></div>}
      {b.decisions.length > 0 && <div className="corp-brief-block"><h3>Decisions this week</h3>
        <ul className="corp-list">{b.decisions.map((d) => <li key={d.id}><Link className="corp-link" to={`/app/corp/decisions?d=${d.id}`}>{d.title}</Link>
          <span className="corp-row-sub"> · {d.unit_name}{d.version > 1 ? ` · changed (v${d.version})` : ''}</span></li>)}</ul></div>}
      {(b.owe.length > 6 || b.owed.length > 6) && <Link className="corp-link" to="/app/corp/commitments">See all commitments</Link>}
    </section>
  );
}

export function WorkNote({ corp }) {
  const [n, setN] = useState(null);
  useEffect(() => { corpApi.workNote(corp.data.org.id).then(setN).catch(() => {}); }, [corp.data.org.id]);
  if (!n) return null;
  const save = async (e) => {
    e.preventDefault();
    try { setN(await corpApi.saveWorkNote(corp.data.org.id, n.note, n.sharing)); showToast('Saved.', 'success'); } catch (err) { showToast(err.message, 'error'); }
  };
  return (
    <form className="corp-card corp-form" onSubmit={save}>
      <h2>Your work note</h2>
      <p className="corp-muted corp-small">Optional context for “Find common ground”, e.g. “On leave next week” or “I prefer async reviews”.
        It’s used only when everyone in the discussion has chosen to share theirs.</p>
      <textarea rows={2} aria-label="Work note" maxLength={600} value={n.note} onChange={(e) => setN({ ...n, note: e.target.value })} />
      <label className="consent-check"><input type="checkbox" checked={n.sharing} onChange={(e) => setN({ ...n, sharing: e.target.checked })} /> Share it in common ground</label>
      <div className="corp-actions"><button className="corp-btn">Save</button></div>
    </form>
  );
}

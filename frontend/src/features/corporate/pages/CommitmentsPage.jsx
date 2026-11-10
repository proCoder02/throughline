// The ledger: what I owe, what I'm owed, and each team's commitments.
import { useCallback, useEffect, useState } from 'react';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { CommitmentRow, UnitSelect, myUnits } from '../components/work.jsx';

function AddCommitment({ corp, onAdded }) {
  const [text, setText] = useState('');
  const [unitId, setUnitId] = useState(null);
  const [due, setDue] = useState('');
  const [owedTo, setOwedTo] = useState('');
  const [people, setPeople] = useState([]);
  useEffect(() => { corpApi.members(corp.data.org.id).then((d) => setPeople(d.members)).catch(() => {}); }, [corp.data.org.id]);
  const add = async (e) => {
    e.preventDefault();
    try {
      await corpApi.addCommitment(corp.data.org.id, { text, unit_id: unitId, due_date: due || null, owed_to_id: owedTo ? Number(owedTo) : null });
      setText(''); setDue(''); setOwedTo(''); onAdded();
    } catch (err) { showToast(err.message, 'error'); }
  };
  return (
    <form className="corp-card corp-form" onSubmit={add}>
      <h2>Add a commitment</h2>
      <input aria-label="What you will do" placeholder="What will you do?" value={text} onChange={(e) => setText(e.target.value)} maxLength={500} />
      <div className="corp-inline wrap">
        <UnitSelect corp={corp} value={unitId} onChange={setUnitId} allowNone="Just me" />
        <label className="corp-field compact"><span>Owed to</span>
          <select value={owedTo} onChange={(e) => setOwedTo(e.target.value)}><option value="">No one in particular</option>
            {people.map((p) => <option key={p.user_id} value={p.user_id}>{p.username}</option>)}</select></label>
        <label className="corp-field compact"><span>Due</span><input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        <button className="corp-btn primary" disabled={!text.trim()}>Add</button>
      </div>
    </form>
  );
}

export default function CommitmentsPage({ corp }) {
  const [scope, setScope] = useState('mine');
  const [unitId, setUnitId] = useState(myUnits(corp)[0]?.id ?? null);
  const [list, setList] = useState(null);
  const load = useCallback(() => corpApi.commitments(corp.data.org.id, scope, scope === 'team' ? unitId : null)
    .then((d) => setList(d.commitments)).catch(() => setList([])), [corp.data.org.id, scope, unitId]);
  useEffect(() => { setList(null); load(); }, [load]);
  const tabs = [['mine', 'I owe'], ['owed', 'Owed to me'], ['team', 'Team']];
  return (
    <div className="corp-page">
      <header className="corp-page-head"><div><h1>Commitments</h1>
        <p className="corp-muted">Promises from meetings and chat stay open until there’s evidence they were done.</p></div></header>
      <AddCommitment corp={corp} onAdded={load} />
      <section className="corp-card">
        <div className="corp-inline wrap">
          <div className="corp-seg" role="group" aria-label="Show">
            {tabs.map(([k, l]) => <button key={k} className={scope === k ? 'active' : ''} aria-pressed={scope === k} onClick={() => setScope(k)}>{l}</button>)}
          </div>
          {scope === 'team' && <UnitSelect corp={corp} value={unitId} onChange={setUnitId} />}
        </div>
        {!list ? <div className="corp-spinner small" /> : list.length === 0 ? <p className="corp-muted">Nothing here.</p>
          : <ul className="corp-list">{list.map((c) => <CommitmentRow key={c.id} corp={corp} c={c} onChanged={load} />)}</ul>}
      </section>
    </div>
  );
}

// Decision log: one current version per decision, every earlier one kept.
import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { UnitSelect, fmtDate, myUnits } from '../components/work.jsx';

function Decision({ corp, id, onChanged }) {
  const [d, setD] = useState(null);
  const [text, setText] = useState('');
  const load = useCallback(() => corpApi.decision(corp.data.org.id, id).then(setD).catch((e) => showToast(e.message, 'error')), [corp.data.org.id, id]);
  useEffect(() => { load(); }, [load]);
  if (!d) return <div className="corp-spinner small" />;
  const change = async (e) => {
    e.preventDefault();
    try { setD(await corpApi.addVersion(corp.data.org.id, id, text)); setText(''); onChanged(); } catch (err) { showToast(err.message, 'error'); }
  };
  return (
    <div className="corp-panel">
      <header className="corp-panel-head"><div><div className="corp-eyebrow">Version {d.decision.version}</div><h2>{d.decision.title}</h2></div></header>
      <div className="corp-panel-section">
        <ol className="corp-versions">{d.versions.map((v) => (
          <li key={v.version} className={v.version === d.decision.version ? 'current' : ''}>
            <div className="corp-row-sub">v{v.version} · {fmtDate(v.created_at)}{v.made_by ? ` · ${v.made_by}` : v.by ? ` · ${v.by}` : ''} · from {v.source_type.replace('_', ' ')}</div>
            <div>{v.text}</div>{v.quote && <div className="corp-quote">“{v.quote}”</div>}
          </li>))}</ol>
      </div>
      {d.decision.can_edit && (
        <form className="corp-panel-section corp-form" onSubmit={change}>
          <h3>It changed</h3>
          <textarea rows={2} aria-label="New decision" value={text} onChange={(e) => setText(e.target.value)} placeholder="What's decided now (the old version is kept)" />
          <div className="corp-actions"><button className="corp-btn primary" disabled={!text.trim()}>Save new version</button></div>
        </form>
      )}
    </div>
  );
}

export default function DecisionsPage({ corp }) {
  const [params, setParams] = useSearchParams();
  const [list, setList] = useState(null);
  const [unitId, setUnitId] = useState(myUnits(corp)[0]?.id ?? null);
  const [text, setText] = useState('');
  const orgId = corp.data.org.id;
  const load = useCallback(() => corpApi.decisions(orgId).then((d) => setList(d.decisions)).catch(() => setList([])), [orgId]);
  useEffect(() => { load(); }, [load]);
  const open = Number(params.get('d')) || null;
  const add = async (e) => {
    e.preventDefault();
    try { const r = await corpApi.addDecision(orgId, { unit_id: unitId, text }); setText(''); await load(); setParams({ d: String(r.id) }); }
    catch (err) { showToast(err.message, 'error'); }
  };
  return (
    <div className="corp-page wide">
      <header className="corp-page-head"><div><h1>Decisions</h1><p className="corp-muted">What each team decided, with every change kept.</p></div></header>
      {myUnits(corp).length > 0 && (
        <form className="corp-card corp-inline wrap" onSubmit={add}>
          <UnitSelect corp={corp} value={unitId} onChange={setUnitId} />
          <label className="corp-field compact" style={{ flex: 1 }}><span>Decision</span>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. We'll target PostgreSQL 17" /></label>
          <button className="corp-btn primary" disabled={!text.trim() || !unitId}>Log</button>
        </form>
      )}
      <div className={'corp-split' + (open ? ' has-selection' : '')}>
        <div className="corp-card corp-tree-card">
          {!list ? <div className="corp-spinner small" /> : list.length === 0 ? <p className="corp-muted corp-pad">No decisions yet.</p> : (
            <ul className="corp-tree">{list.map((d) => (
              <li key={d.id}><button className={'corp-tree-row' + (d.id === open ? ' active' : '')} onClick={() => setParams({ d: String(d.id) })}>
                <span className="corp-tree-name">{d.title}<span className="corp-row-sub corp-ellipsis">{d.unit_name} · v{d.version} · {fmtDate(d.updated_at)}</span></span>
              </button></li>))}</ul>
          )}
        </div>
        {open ? <Decision key={open} corp={corp} id={open} onChanged={load} /> : <div className="corp-card corp-panel-empty"><p className="corp-muted">Pick a decision to see its history.</p></div>}
      </div>
    </div>
  );
}

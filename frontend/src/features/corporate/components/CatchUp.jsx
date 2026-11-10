// "Catch me up" / meeting prep: an AI brief built only from what the viewer may
// see (meetings, decisions, commitments, blockers), with links to its sources.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';

const LINK = { meeting: (s) => `/app/corp/meetings/${s.id}`, decision: (s) => `/app/corp/decisions?d=${s.id}`, commitment: () => '/app/corp/commitments' };

export default function CatchUp({ corp, label = 'Catch me up', params = {}, periods = true }) {
  const [days, setDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const run = async (d = days) => {
    setBusy(true);
    try { setRes(await corpApi.catchUp(corp.data.org.id, { days: d, ...params })); } catch (e) { showToast(e.message, 'error'); }
    finally { setBusy(false); }
  };
  return (
    <div className="cu">
      <div className="corp-inline wrap">
        <button className="corp-btn primary" onClick={() => run()} disabled={busy}>{busy ? 'Reading…' : res ? 'Refresh' : label}</button>
        {periods && (
          <div className="corp-seg" role="group" aria-label="Period">
            {[[1, 'Today'], [7, '7 days'], [14, '14 days']].map(([d, l]) => (
              <button key={d} className={days === d ? 'active' : ''} aria-pressed={days === d} onClick={() => { setDays(d); if (res) run(d); }}>{l}</button>))}
          </div>)}
        {res && <button className="corp-link" onClick={() => setRes(null)}>Close</button>}
      </div>
      {res && (
        <div className="cu-body" aria-live="polite">
          <p className="corp-answer">{res.brief}</p>
          {res.sources.length > 0 && (
            <div className="cu-sources">{res.sources.map((s) => (
              <Link key={s.ref} className="corp-tag" to={LINK[s.type](s)} title={s.title}>[{s.ref}] {s.title}</Link>))}</div>)}
        </div>)}
    </div>
  );
}

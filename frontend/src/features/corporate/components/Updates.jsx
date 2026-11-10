// One commitment's dependencies and progress notes: what it supports, the
// supporting work from other teams (owner, date vs. asked date, blocker), and
// the update/blocker feed anyone who can see it may add to.
import { useCallback, useEffect, useState } from 'react';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { StatusChip, fmtDate } from './work.jsx';

export default function Updates({ corp, c, onChanged }) {
  const orgId = corp.data.org.id;
  const [d, setD] = useState(null);
  const [kind, setKind] = useState('update');
  const [text, setText] = useState('');
  const load = useCallback(() => corpApi.updates(orgId, c.id).then(setD).catch((e) => showToast(e.message, 'error')), [orgId, c.id]);
  useEffect(() => { load(); }, [load]);
  const post = async (e) => {
    e.preventDefault();
    try { setD(await corpApi.postUpdate(orgId, c.id, kind, text)); setText(''); setKind('update'); onChanged?.(); }
    catch (err) { showToast(err.message, 'error'); }
  };
  if (!d) return <div className="corp-spinner small" aria-label="Loading" />;
  const blocked = d.updates.find((u) => u.kind === 'blocker' || u.kind === 'unblocked')?.kind === 'blocker';
  return (
    <div className="upd">
      {d.supports && !d.supports.hidden && (
        <p className="corp-small corp-muted">Supports: <strong>{d.supports.text}</strong>{d.supports.due_date ? ` · due ${fmtDate(d.supports.due_date)}` : ''}</p>
      )}
      {d.supporting.length > 0 && (
        <div className="upd-deps">
          <h4>Depends on</h4>
          <ul>{d.supporting.map((k) => (
            <li key={k.id} className={k.blocker || k.status === 'overdue' || k.status === 'declined' ? 'bad' : ''}>
              <span className="upd-dep-main"><strong>{k.unit_name}</strong>: {k.text}
                <span className="corp-row-sub"> · {k.owner || 'no owner yet'} · {k.due_date ? `due ${fmtDate(k.due_date)}` : 'no date'}
                  {k.requested_due && k.due_date && k.due_date > k.requested_due ? ` (asked for ${fmtDate(k.requested_due)})` : ''}</span>
                {k.blocker && <span className="upd-blocker">Blocked: {k.blocker}</span>}
              </span>
              <StatusChip status={k.status} />
            </li>))}
          </ul>
        </div>
      )}
      <form className="upd-form" onSubmit={post}>
        <select aria-label="Kind of update" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="update">Progress update</option>
          <option value="blocker">Blocked</option>
          {blocked && <option value="unblocked">Unblocked</option>}
        </select>
        <input aria-label="Update" value={text} onChange={(e) => setText(e.target.value)} maxLength={1000}
          placeholder={kind === 'blocker' ? 'What is blocking it, and what you need' : 'What changed'} />
        <button className="corp-btn small primary" disabled={!text.trim()}>Post</button>
      </form>
      {d.updates.length > 0 && (
        <ol className="upd-feed">{d.updates.map((u) => (
          <li key={u.id} className={u.kind}>
            <span className="upd-kind">{u.kind === 'blocker' ? 'Blocked' : u.kind === 'unblocked' ? 'Unblocked' : 'Update'}</span>
            {u.text} <span className="corp-row-sub">· {u.author} · {fmtDate(u.created_at)}</span>
          </li>))}
        </ol>
      )}
    </div>
  );
}

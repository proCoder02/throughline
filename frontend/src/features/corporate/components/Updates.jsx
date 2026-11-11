// A commitment's activity thread (Jira / Linear / GitHub-issue style): what it
// depends on, then everything that happened -- events in plain sentences and
// people's notes as messages, oldest first -- and the composer at the bottom.
import { TicketProgress } from './tickets.jsx';
import { useCallback, useEffect, useRef, useState } from 'react';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import Avatar from '../../../components/Avatar.jsx';
import { StatusChip, fmtDate } from './work.jsx';

const when = (iso) => new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });

// System events as sentences people would write themselves.
function sentence(e) {
  const d = e.detail || {};
  const who = e.actor || 'Throughline';
  switch (e.kind) {
    case 'created': return `${who} created this${d.source === 'meeting' ? ' from a meeting' : d.source === 'thread' ? ' from a chat' : d.source === 'request' ? ' as a request' : ''}`;
    case 'confirm': return `${who} confirmed it`;
    case 'done': return d.by === 'evidence check' ? `Throughline found it was done${d.evidence ? `: “${d.evidence}”` : ''}`
      : `${who} marked it done${d.note && d.note !== 'Marked done' ? `: ${d.note}` : ''}`;
    case 'drop': return `${who} dropped it${d.reason ? `: ${d.reason}` : ''}`;
    case 'reopen': return `${who} reopened it`;
    case 'edit': return d.due_date !== undefined ? `${who} moved the due date to ${d.due_date ? fmtDate(d.due_date) : 'none'}`
      : d.owner_id !== undefined ? `${who} changed the owner` : `${who} reworded it`;
    case 'overdue': return 'It became overdue';
    case 'at_risk': return `At risk: ${d.why || ''}`;
    case 'risk_cleared': return 'No longer at risk';
    case 'accepted': return `${who} accepted the request${d.due_date ? `, due ${fmtDate(d.due_date)}` : ''}${d.asked_for ? ` (asked for ${fmtDate(d.asked_for)})` : ''}`;
    case 'declined': return `${who} declined${d.reason ? `: ${d.reason}` : ''}`;
    case 'reassigned': return `Handed over from ${d.from_user || 'a former member'}`;
    default: return `${who}: ${e.kind.replace(/_/g, ' ')}`;
  }
}
const TONE = { at_risk: 'bad', overdue: 'bad', declined: 'bad', done: 'good', risk_cleared: 'good' };

export default function Updates({ corp, c, onChanged }) {
  const orgId = corp.data.org.id;
  const [d, setD] = useState(null);
  const [kind, setKind] = useState('update');
  const [text, setText] = useState('');
  const end = useRef(null);
  const load = useCallback(() => corpApi.updates(orgId, c.id).then(setD).catch((e) => showToast(e.message, 'error')), [orgId, c.id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [d?.thread?.length]);
  const post = async (e) => {
    e.preventDefault();
    try { setD(await corpApi.postUpdate(orgId, c.id, kind, text)); setText(''); setKind('update'); onChanged?.(); }
    catch (err) { showToast(err.message, 'error'); }
  };
  if (!d) return <div className="corp-spinner small" aria-label="Loading" />;
  const blocked = [...d.thread].reverse().find((t) => t.type === 'note' && t.kind !== 'update')?.kind === 'blocker';

  return (
    <div className="act">
      {d.supports && !d.supports.hidden && (
        <p className="corp-small corp-muted">Supports <strong>{d.supports.text}</strong>{d.supports.due_date ? ` · due ${fmtDate(d.supports.due_date)}` : ''}</p>
      )}
      {d.tickets && <div className="upd-deps"><h4>Tickets</h4><TicketProgress t={d.tickets} /></div>}
      {d.supporting.length > 0 && (
        <div className="upd-deps">
          <h4>Depends on</h4>
          <ul>{d.supporting.map((k) => (
            <li key={k.id}>
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

      <ol className="act-thread" aria-label="Activity">
        {d.thread.map((t, i) => t.type === 'event' ? (
          <li key={i} className={'act-event ' + (TONE[t.kind] || '')}>
            <span className="act-dot" aria-hidden="true" />
            <span className="act-text">{sentence(t)}</span>
            <time className="act-time" dateTime={t.at}>{when(t.at)}</time>
          </li>
        ) : (
          <li key={i} className={'act-note ' + t.kind}>
            <Avatar name={t.actor} />
            <div className="act-bubble">
              <div className="act-head"><strong>{t.actor}</strong>
                {t.kind !== 'update' && <span className={'act-tag ' + t.kind}>{t.kind === 'blocker' ? 'Blocked' : 'Unblocked'}</span>}
                <time className="act-time" dateTime={t.at}>{when(t.at)}</time></div>
              <div className="act-body">{t.text}</div>
            </div>
          </li>
        ))}
        <li ref={end} aria-hidden="true" />
      </ol>

      {d.closed ? (
        <p className="corp-small corp-muted">This is closed. Reopen it to add updates.</p>
      ) : (
        <form className="act-compose" onSubmit={post}>
          <div className="corp-seg" role="group" aria-label="Type of note">
            {[['update', 'Update'], ['blocker', 'Blocked'], ...(blocked ? [['unblocked', 'Unblocked']] : [])].map(([k, l]) => (
              <button type="button" key={k} className={kind === k ? 'active' : ''} aria-pressed={kind === k} onClick={() => setKind(k)}>{l}</button>))}
          </div>
          <div className="corp-inline">
            <input aria-label="Note" value={text} onChange={(e) => setText(e.target.value)} maxLength={1000}
              placeholder={kind === 'blocker' ? 'What’s blocking it, and what you need' : kind === 'unblocked' ? 'What resolved it' : 'Add an update'} />
            <button className="corp-btn small primary" disabled={!text.trim()}>Post</button>
          </div>
        </form>
      )}
    </div>
  );
}

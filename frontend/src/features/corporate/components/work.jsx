// Small pieces shared by the work-memory pages.
import { lazy, Suspense, useState } from 'react';
import { corpApi } from '../corpApi.js';
const Updates = lazy(() => import('./Updates.jsx'));
import { showToast } from '../../../lib/notify.js';

export const STATUS_LABEL = { requested: 'Requested', detected: 'To confirm', open: 'Open', overdue: 'Overdue', done: 'Done',
  dropped: 'Dropped', declined: 'Declined' };
export const fmtDate = (d) => (d ? new Date(d.length === 10 ? d + 'T00:00:00' : d).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '');

export function StatusChip({ status, atRisk }) {
  return (
    <span className="corp-status-wrap">
      <span className={'corp-status ' + status}>{STATUS_LABEL[status] || status}</span>
      {atRisk && <span className="corp-status at-risk">At risk</span>}
    </span>
  );
}

// Teams the user is in -- where they can add meetings, decisions and team commitments.
export const myUnits = (corp) => corp.data.units.filter((u) => u.my_role);

export function UnitSelect({ corp, value, onChange, allowNone, label = 'Team' }) {
  const units = myUnits(corp);
  return (
    <label className="corp-field compact">
      <span>{label}</span>
      <select value={value ?? ''} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
        {allowNone && <option value="">{allowNone}</option>}
        {!allowNone && !value && <option value="">Choose…</option>}
        {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
      </select>
    </label>
  );
}

export function CommitmentRow({ corp, c, onChanged }) {
  const orgId = corp.data.org.id;
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState(null);
  const [showUpdates, setShowUpdates] = useState(false);
  const act = async (action, data) => {
    setBusy(true);
    try { await corpApi.commitmentAction(orgId, c.id, action, data); onChanged?.(); }
    catch (e) { showToast(e.message, 'error'); }
    finally { setBusy(false); }
  };
  const done = () => { const note = window.prompt('How was it done? (optional)', ''); if (note !== null) act('done', { note }); };
  const drop = () => { const reason = window.prompt('Why is it dropped?', ''); if (reason) act('drop', { reason }); };
  const due = () => { const v = window.prompt('New due date (YYYY-MM-DD)', c.due_date || ''); if (v !== null) act('edit', { due_date: v || null }); };
  const toggleHistory = async () => {
    if (history) { setHistory(null); return; }
    try { setHistory((await corpApi.commitment(orgId, c.id)).history); } catch (e) { showToast(e.message, 'error'); }
  };
  const live = ['detected', 'open', 'overdue'].includes(c.status);
  return (
    <li className="corp-commit">
      <div className="corp-commit-main">
        <div className="corp-row-title">{c.text}</div>
        <div className="corp-row-sub">
          {c.owner || c.owner_name || 'No owner'}{(c.owed_to || c.owed_to_name) ? ` → ${c.owed_to || c.owed_to_name}` : ''}
          {c.due_date ? ` · due ${fmtDate(c.due_date)}` : ''}{c.unit_name ? ` · ${c.unit_name}` : ''}
          {c.contact_name ? ` · for ${c.contact_name}` : ''}{c.project_name ? ` · ${c.project_name}` : ''}
        </div>
        {c.risk_note && <div className="corp-risk">At risk: {c.risk_note}</div>}
        {c.evidence && <div className="corp-evidence">✓ {c.evidence}</div>}
        {c.quote && !c.evidence && <div className="corp-quote">“{c.quote}”</div>}
        {history && (
          <ol className="corp-history">
            {history.map((h, i) => <li key={i}>{fmtDate(h.at)} · {h.kind}{h.actor ? ` by ${h.actor}` : ''}{h.detail?.reason ? `: ${h.detail.reason}` : ''}{h.detail?.due_date ? ` → ${fmtDate(h.detail.due_date)}` : ''}</li>)}
          </ol>
        )}
      </div>
      <StatusChip status={c.status} atRisk={c.at_risk || !!c.risk_note} />
      <div className="corp-commit-actions">
        {c.can_change && c.status === 'detected' && <button className="corp-btn small primary" disabled={busy} onClick={() => act('confirm')}>Confirm</button>}
        {c.can_change && live && <button className="corp-btn small" disabled={busy} onClick={done}>Done</button>}
        {c.can_change && live && <button className="corp-btn small" disabled={busy} onClick={due}>Due date</button>}
        {c.can_change && (live || c.status === 'requested') && (
          <button className="corp-btn small" disabled={busy} onClick={drop}>{c.status === 'requested' ? 'Withdraw' : 'Drop'}</button>)}
        {c.can_change && ['done', 'dropped'].includes(c.status) && <button className="corp-btn small" disabled={busy} onClick={() => act('reopen')}>Reopen</button>}
        <button className="corp-link" onClick={() => setShowUpdates((v) => !v)}>
          {showUpdates ? 'Hide updates' : c.supporting_count > 0 ? `Updates · depends on ${c.supporting_count}` : 'Updates'}</button>
        <button className="corp-link" onClick={toggleHistory}>{history ? 'Hide history' : 'History'}</button>
      </div>
      {showUpdates && <div className="corp-commit-updates"><Suspense fallback={null}><Updates corp={corp} c={c} onChanged={onChanged} /></Suspense></div>}
    </li>
  );
}

// The shared "common ground" result, with one tap to log an agreed point as a decision.
export function CommonGroundCard({ corp, id, result, usedNotes }) {
  const [logged, setLogged] = useState({});
  const log = async (text) => {
    try { await corpApi.decisionFromCommonGround(corp.data.org.id, id, text); setLogged((l) => ({ ...l, [text]: true })); showToast('Logged as a decision.', 'success'); }
    catch (e) { showToast(e.message, 'error'); }
  };
  const Section = ({ title, items, canLog }) => (items?.length ? (
    <div className="corp-cg-section">
      <h4>{title}</h4>
      <ul>{items.map((t) => (
        <li key={t}>{t}{canLog && id && (logged[t] ? <span className="corp-tag">Logged</span>
          : <button className="corp-link corp-cg-log" onClick={() => log(t)}>Log as decision</button>)}</li>))}</ul>
    </div>) : null);
  return (
    <div className="corp-cg">
      <div className="corp-cg-head">Common ground{usedNotes ? ' · includes shared work notes' : ''}</div>
      {result.positions?.length > 0 && (
        <div className="corp-cg-section"><h4>Where people stand</h4>
          <ul>{result.positions.map((p, i) => <li key={i}><strong>{p.name}</strong>: {p.position}</li>)}</ul></div>
      )}
      <Section title="Agreed" items={result.agreed} canLog />
      <Section title="Still open" items={result.open} />
      <Section title="Options" items={result.options} canLog />
    </div>
  );
}

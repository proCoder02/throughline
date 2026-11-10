// Organisation overview: size, my place in it, what to set up next, and the
// organisation's own settings (rename / delete / leave).
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { corpApi, managerLabel, ORG_ROLE_LABELS } from '../corpApi.js';
import { confirmDialog, showToast } from '../../../lib/notify.js';
import { CheckIcon, ChevronRightIcon } from '../../../icons.jsx';
import { Brief, WorkNote } from '../components/Brief.jsx';

function Stat({ value, label }) {
  return (
    <div className="corp-stat">
      <div className="corp-stat-value">{value}</div>
      <div className="corp-stat-label">{label}</div>
    </div>
  );
}

function OrgSettings({ corp }) {
  const navigate = useNavigate();
  const { org, me } = corp.data;
  const [name, setName] = useState(org.name);
  const [confirmName, setConfirmName] = useState('');
  const [busy, setBusy] = useState(false);

  const run = async (fn) => {
    setBusy(true);
    try { await fn(); } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };

  const rename = (e) => {
    e.preventDefault();
    run(async () => { corp.apply(await corpApi.renameOrg(org.id, name)); showToast('Renamed.', 'success'); });
  };
  const remove = () => run(async () => {
    const ok = await confirmDialog({
      title: `Delete ${org.name}?`, danger: true, confirmLabel: 'Delete organisation',
      message: 'Its departments, teams, memberships and invitations are deleted for everyone. This can’t be undone.',
    });
    if (!ok) return;
    await corpApi.deleteOrg(org.id, confirmName);
    showToast(`${org.name} was deleted.`, 'success');
    await corp.reload();
    navigate('/app/corp');
  });
  const leave = () => run(async () => {
    const ok = await confirmDialog({ title: `Leave ${org.name}?`, confirmLabel: 'Leave', danger: true,
      message: 'You’ll lose access until someone invites you again.' });
    if (!ok) return;
    await corpApi.leaveOrg(org.id);
    await corp.reload();
    navigate('/app/corp');
  });

  return (
    <section className="corp-card">
      <h2>Organisation settings</h2>
      {me.org_admin && (
        <form className="corp-inline" onSubmit={rename}>
          <input aria-label="Organisation name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          <button className="corp-btn" disabled={busy || !name.trim() || name.trim() === org.name}>Rename</button>
        </form>
      )}
      {me.org_owner ? (
        <div className="corp-danger">
          <p>Delete the organisation for everyone. Type <strong>{org.name}</strong> to confirm.</p>
          <div className="corp-inline">
            <input aria-label="Organisation name to confirm" value={confirmName} onChange={(e) => setConfirmName(e.target.value)} />
            <button className="corp-btn danger" disabled={busy || confirmName.trim().toLowerCase() !== org.name.toLowerCase()}
              onClick={remove}>Delete</button>
          </div>
        </div>
      ) : (
        <button className="corp-btn danger" onClick={leave} disabled={busy}>Leave organisation</button>
      )}
    </section>
  );
}

export default function OverviewPage({ corp }) {
  const navigate = useNavigate();
  const { org, me, units } = corp.data;
  const departments = units.filter((u) => u.kind === 'department');
  const teams = units.filter((u) => u.kind === 'team');
  const mine = units.filter((u) => u.my_role);

  const steps = [
    { done: departments.length > 0, text: 'Add your departments', to: '/app/corp/structure' },
    { done: departments.length > 0 && departments.every((d) => d.managers.length), text: 'Appoint a head for each department', to: '/app/corp/structure' },
    { done: teams.length > 0, text: 'Add teams under departments', to: '/app/corp/structure' },
    { done: org.member_count > 1, text: 'Invite your colleagues', to: '/app/corp/people' },
  ];

  return (
    <div className="corp-page">
      <header className="corp-page-head">
        <div>
          <h1>{org.name}</h1>
          <p className="corp-muted">You’re {me.role === 'owner' ? 'the owner' : `${me.role === 'admin' ? 'an' : 'a'} ${ORG_ROLE_LABELS[me.role].toLowerCase()}`} here.</p>
        </div>
      </header>

      <Brief corp={corp} />

      <div className="corp-stats">
        <Stat value={org.member_count} label={org.member_count === 1 ? 'person' : 'people'} />
        <Stat value={departments.length} label={departments.length === 1 ? 'department' : 'departments'} />
        <Stat value={teams.length} label={teams.length === 1 ? 'team' : 'teams'} />
      </div>

      {me.org_admin && steps.some((s) => !s.done) && (
        <section className="corp-card">
          <h2>Get set up</h2>
          <ol className="corp-steps">
            {steps.map((s) => (
              <li key={s.text} className={s.done ? 'done' : ''}>
                <span className="corp-step-mark" aria-hidden="true">{s.done ? <CheckIcon /> : null}</span>
                <button className="corp-link" onClick={() => navigate(s.to)}>{s.text}</button>
                <span className="sr-only">{s.done ? '(done)' : '(to do)'}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section className="corp-card">
        <h2>Where you are</h2>
        {mine.length === 0 ? (
          <p className="corp-muted">You aren’t in a department or team yet.</p>
        ) : (
          <ul className="corp-list">
            {mine.map((u) => (
              <li key={u.id}>
                <button className="corp-row" onClick={() => navigate(`/app/corp/structure?unit=${u.id}`)}>
                  <span className="corp-row-main">
                    <span className="corp-row-title">{u.name}</span>
                    <span className="corp-row-sub">
                      {u.kind === 'team' ? 'Team' : 'Department'} · {u.my_role === 'manager' ? managerLabel(u.kind) : 'Member'}
                    </span>
                  </span>
                  <ChevronRightIcon aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <WorkNote corp={corp} />

      <OrgSettings key={org.id} corp={corp} />
    </div>
  );
}

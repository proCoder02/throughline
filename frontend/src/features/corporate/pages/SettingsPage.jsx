// Corporate settings: the organisation's own settings (rename / delete /
// leave) and your opt-in work note. Kept off the home screen on purpose:
// rarely used, and deleting is destructive.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { corpApi, ORG_ROLE_LABELS } from '../corpApi.js';
import { confirmDialog, showToast } from '../../../lib/notify.js';
import { WorkNote } from '../components/Brief.jsx';

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

export default function SettingsPage({ corp }) {
  const { org, me } = corp.data;
  return (
    <div className="corp-page narrow">
      <header className="corp-page-head"><div><h1>Settings</h1>
        <p className="corp-muted">{org.name} · you’re {me.role === 'owner' ? 'the owner' : `${me.role === 'admin' ? 'an' : 'a'} ${ORG_ROLE_LABELS[me.role].toLowerCase()}`}</p></div></header>
      <WorkNote corp={corp} />
      <OrgSettings key={org.id} corp={corp} />
    </div>
  );
}

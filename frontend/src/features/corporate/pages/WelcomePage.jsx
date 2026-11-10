// What someone sees in Corporate before they belong to any organisation.
// Joining is by invitation only: an invitation to their email shows as
// "Join"; without one they're told to ask their admin. Creating a new
// organisation is a deliberate, secondary choice (and can be switched off:
// CORPORATE_ORG_CREATION=closed).
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { corpApi, managerLabel } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { switchMode } from '../../../lib/mode.js';
import { BuildingIcon, MailIcon } from '../../../icons.jsx';
import SetupPage from './SetupPage.jsx';

export default function WelcomePage({ corp }) {
  const navigate = useNavigate();
  const [mine, setMine] = useState(null); // {email, invites}
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(null);

  useEffect(() => { corpApi.myInvites().then(setMine).catch(() => setMine({ email: '', invites: [] })); }, []);

  const join = async (inv) => {
    setBusy(inv.id);
    try {
      const r = await corpApi.acceptMine(inv.id);
      await switchMode('corporate').catch(() => {});
      await corp.reload(r.org_id);
      showToast(`You've joined ${inv.org_name}.`, 'success');
      navigate('/app/corp');
    } catch (e) {
      showToast(e.message, 'error');
      setBusy(null);
    }
  };

  if (creating) return <SetupPage corp={corp} first onCancel={() => setCreating(false)} />;
  if (!mine) return <div className="corp-center"><div className="corp-spinner" aria-label="Loading" /></div>;

  return (
    <div className="corp-page narrow">
      <header className="corp-page-head">
        <BuildingIcon className="corp-head-icon" aria-hidden="true" />
        <div><h1>Corporate</h1><p className="corp-muted">Your company’s teams, meetings and commitments, in one place.</p></div>
      </header>

      {corp.suspended.length > 0 && (
        <p className="corp-warn">An admin has paused your access to {corp.suspended.map((o) => o.name).join(', ')}.</p>
      )}

      {mine.invites.length > 0 ? (
        <section className="corp-card">
          <h2>You’ve been invited</h2>
          <ul className="corp-list">{mine.invites.map((inv) => (
            <li key={inv.id} className="corp-person">
              <span className="corp-row-main">
                <span className="corp-row-title">{inv.org_name}</span>
                <span className="corp-row-sub">
                  {inv.invited_by ? `Invited by ${inv.invited_by}` : 'Invited'}
                  {inv.unit_name ? ` · ${inv.unit_name}${inv.unit_role === 'manager' ? ` as ${managerLabel(inv.unit_kind).toLowerCase()}` : ''}` : ''}
                  {inv.role === 'admin' ? ' · admin' : ''}
                </span>
              </span>
              <button className="corp-btn primary" onClick={() => join(inv)} disabled={busy !== null}>
                {busy === inv.id ? 'Joining…' : 'Join'}
              </button>
            </li>))}
          </ul>
        </section>
      ) : (
        <section className="corp-card corp-ask-admin">
          <MailIcon className="corp-empty-icon" aria-hidden="true" />
          <h2>Joining your company?</h2>
          <p>Ask your Throughline admin to invite this email address:</p>
          <p className="corp-email">{mine.email || 'your account email'}</p>
          <p className="corp-muted corp-small">The invitation will appear here and in your inbox. Not the right address? Change it in Settings, or sign in with your work account.</p>
          <button className="corp-btn" onClick={() => corpApi.myInvites().then(setMine).catch(() => {})}>Check again</button>
        </section>
      )}

      {corp.canCreate && (
        <section className="corp-card corp-secondary">
          <div className="corp-row-main">
            <span className="corp-row-title">Setting up Throughline for your company?</span>
            <span className="corp-row-sub">Create a new organisation. You’ll be its owner and invite everyone else.</span>
          </div>
          <button className="corp-btn" onClick={() => setCreating(true)}>Set up a new organisation</button>
        </section>
      )}
    </div>
  );
}

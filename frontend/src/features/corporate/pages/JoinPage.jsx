// Opened from an invitation link (/app/corp/join/<token>). The person is
// already signed in here (App shows the sign-in screen first, and keeps
// this URL). Only the account with the invited email can accept.
import WorkEmailConfirm from '../components/WorkEmailConfirm.jsx';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { corpApi, managerLabel } from '../corpApi.js';
import { BuildingIcon } from '../../../icons.jsx';
import { switchMode } from '../../../lib/mode.js';

export default function JoinPage({ user, onJoined }) {
  const { token } = useParams();
  const navigate = useNavigate();
  const [invite, setInvite] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    corpApi.previewInvite(token).then(setInvite).catch((e) => setError(e.message));
  }, [token]);

  const accept = async () => {
    setBusy(true);
    try {
      const r = await corpApi.acceptInvite(token);
      // They joined a company: open on the Corporate side from now on.
      await switchMode('corporate').catch(() => {});
      await onJoined(r.org_id);
      navigate('/app/corp', { replace: true });
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  let content;
  if (error && !invite) {
    content = <><h2>This invitation can’t be used</h2><p className="corp-muted">{error}</p></>;
  } else if (!invite) {
    content = <div className="corp-spinner" aria-label="Loading" />;
  } else if (invite.already_member && invite.status !== 'pending') {
    content = (
      <>
        <h2>You’re already in {invite.org_name}</h2>
        <button className="corp-btn primary" onClick={() => { onJoined(invite.org_id); navigate('/app/corp', { replace: true }); }}>
          Open {invite.org_name}
        </button>
      </>
    );
  } else if (invite.status !== 'pending') {
    content = (
      <>
        <h2>This invitation {invite.status === 'accepted' ? 'has already been used' : invite.status === 'expired' ? 'has expired' : 'was withdrawn'}</h2>
        <p className="corp-muted">Ask whoever invited you to {invite.org_name} to send a new one.</p>
      </>
    );
  } else {
    const where = invite.unit_name
      ? ` in ${invite.unit_name}${invite.unit_role === 'manager' ? ` as ${managerLabel(invite.unit_kind).toLowerCase()}` : ''}`
      : '';
    content = (
      <>
        <h2>Join {invite.org_name}</h2>
        <p>You’ve been invited{where}{invite.role === 'admin' ? ', as an admin of the organisation' : ''}.</p>
        {invite.email_matches ? (
          <>
            {error && <p className="corp-warn">{error}</p>}
            <button className="corp-btn primary" onClick={accept} disabled={busy}>{busy ? 'Joining…' : `Join ${invite.org_name}`}</button>
          </>
        ) : (
          <>
            <p className="corp-warn">
              This invitation was sent to {invite.email_masked}, which isn’t on your account yet. If it’s your work email,
              confirm it below and you can join with the account you’re signed in to.
            </p>
            <WorkEmailConfirm hint={invite.email_masked} onConfirmed={() => corpApi.previewInvite(token).then(setInvite).catch((e) => setError(e.message))} />
          </>
        )}
      </>
    );
  }

  return (
    <div className="corp-center">
      <div className="corp-empty">
        <BuildingIcon className="corp-empty-icon" aria-hidden="true" />
        {content}
      </div>
    </div>
  );
}

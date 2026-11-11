// Confirm a work email with a 6-digit code, so invitations sent to it can be
// accepted from this (personal) account. One account, a work identity per
// company: the work email belongs to the membership, not the login.
import { useState } from 'react';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';

export default function WorkEmailConfirm({ onConfirmed, hint }) {
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [sentTo, setSentTo] = useState(null);
  const [busy, setBusy] = useState(false);

  const send = async (e) => {
    e?.preventDefault();
    setBusy(true);
    try {
      const r = await corpApi.startWorkEmail(email.trim());
      if (r.verified) { showToast('That’s already your account email.', 'success'); onConfirmed?.(r.email); return; }
      setSentTo(r.email); setCode('');
      showToast(`Code sent to ${r.masked}`, 'success');
    } catch (err) { showToast(err.message, 'error'); } finally { setBusy(false); }
  };
  const confirm = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await corpApi.verifyWorkEmail(sentTo, code.trim());
      showToast(`${sentTo} confirmed`, 'success');
      setSentTo(null); setEmail(''); setCode('');
      onConfirmed?.(sentTo);
    } catch (err) { showToast(err.message, 'error'); } finally { setBusy(false); }
  };

  if (sentTo) {
    return (
      <form className="corp-form wec" onSubmit={confirm}>
        <label className="corp-field" htmlFor="wec-code"><span>Code sent to {sentTo}</span>
          <input id="wec-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="6-digit code"
            value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} autoFocus /></label>
        <div className="corp-inline wrap">
          <button className="corp-btn primary" disabled={busy || code.length !== 6}>{busy ? 'Checking…' : 'Confirm'}</button>
          <button type="button" className="corp-btn" onClick={send} disabled={busy}>Send a new code</button>
          <button type="button" className="corp-link corp-small" onClick={() => setSentTo(null)}>Use a different email</button>
        </div>
      </form>
    );
  }
  return (
    <form className="corp-form wec" onSubmit={send}>
      <label className="corp-field" htmlFor="wec-email"><span>Your work email{hint ? ` (invited as ${hint})` : ''}</span>
        <input id="wec-email" type="email" autoComplete="email" placeholder="you@company.com" value={email}
          onChange={(e) => setEmail(e.target.value)} /></label>
      <div className="corp-inline wrap">
        <button className="corp-btn primary" disabled={busy || !email.includes('@')}>{busy ? 'Sending…' : 'Send a code'}</button>
        <span className="corp-muted corp-small">We email it a 6-digit code to check it’s yours. Your login stays the same.</span>
      </div>
    </form>
  );
}

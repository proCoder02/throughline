import { useState } from 'react';
import { post } from '../api.js';
import { confirmDialog, showToast } from '../lib/notify.js';

/// Settings "danger zone" (both the desktop Settings screen and the older
/// SettingsSection): password + typing DELETE, one last confirm, then the
/// account and its data are removed server-side and the app signs out.
export default function DeleteAccountCard({ onDeleted }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [confirmText, setConfirmText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (!password) return setError('Enter your password.');
    if (confirmText.trim() !== 'DELETE') return setError('Type DELETE (in capitals) to confirm.');
    const ok = await confirmDialog({
      title: 'Delete your account?',
      message: 'Your account, recordings, notes, chats, messages and files are permanently deleted. This can\'t be undone.',
      confirmLabel: 'Delete permanently', danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const d = await post('/account/delete', { password, confirm: 'DELETE' });
      showToast(d.message || 'Your account has been deleted.', 'success', 8000);
      onDeleted();
    } catch (err) {
      setError(err.message || 'Something went wrong. Please try again.');
      setBusy(false);
    }
  };

  return (
    <section className="danger-zone" aria-labelledby="delete-account-title">
      <h3 id="delete-account-title">Delete account</h3>
      <p>Permanently delete your account and everything stored with it.{' '}
        <a href="/account/delete" target="_blank" rel="noopener">What gets deleted</a></p>
      {!open ? (
        <button type="button" className="btn danger" onClick={() => setOpen(true)}>Delete account…</button>
      ) : (
        <form onSubmit={submit} noValidate>
          <label htmlFor="del-password">Your password</label>
          <input id="del-password" className="field" type="password" autoComplete="current-password"
            value={password} onChange={(e) => setPassword(e.target.value)} />
          <label htmlFor="del-confirm">Type DELETE to confirm</label>
          <input id="del-confirm" className="field" autoComplete="off" value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)} />
          {error && <div className="auth-error" role="alert">{error}</div>}
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button type="button" className="btn secondary" disabled={busy} onClick={() => { setOpen(false); setPassword(''); setConfirmText(''); setError(''); }}>Cancel</button>
            <button type="submit" className="btn danger" disabled={busy}>{busy ? 'Deleting…' : 'Delete my account'}</button>
          </div>
        </form>
      )}
    </section>
  );
}

export function LegalLinks() {
  return (
    <nav className="legal-links" aria-label="Legal">
      <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>
      <a href="/terms" target="_blank" rel="noopener">Terms of Service</a>
    </nav>
  );
}

import { post } from '../api.js';
import { confirmDialog, showToast } from '../lib/notify.js';
import {
  useOutlookStatus, refreshOutlookStatus, OUTLOOK_EXAMPLE_PROMPTS, MICROSOFT_REVOKE_LINKS,
} from '../lib/outlook.js';
import '../styles/mail.css';

// Class names for the two Settings screens' own design systems -- the old
// SettingsSection (detail-card/btn) and OsApp's settings screen
// (card/primary-button). Same content and behavior in both.
const VARIANTS = {
  classic: { card: 'detail-card', title: 'detail-title', sub: 'hint', primary: 'btn', secondary: 'btn secondary' },
  os: { card: 'card', title: null, sub: 'card-sub', primary: 'primary-button', secondary: 'secondary-button' },
};

function Title({ v, children }) {
  return v.title ? <div className={v.title}>{children}</div> : <h3>{children}</h3>;
}

/// Outlook mail link -- hidden entirely when OUTLOOK_ENABLED is off.
/// Connect is a full-page navigation (not fetch): the backend redirects to
/// Microsoft, which redirects back to /app with a result App.jsx toasts.
export default function OutlookSettingsCard({ variant = 'classic', style }) {
  const v = VARIANTS[variant];
  const status = useOutlookStatus();
  if (!status?.enabled) return null;

  const disconnect = async () => {
    const ok = await confirmDialog({
      title: 'Disconnect Outlook?',
      message: 'The assistant will stop reading your mail, and the saved Microsoft sign-in is deleted. You can reconnect any time.',
      confirmLabel: 'Disconnect',
      danger: true,
    });
    if (!ok) return;
    try {
      await post('/integrations/outlook/disconnect', {});
      await refreshOutlookStatus();
      showToast('Outlook disconnected');
    } catch (e) {
      showToast('Could not disconnect: ' + e.message, 'error');
    }
  };

  const connectButton = (label) => (
    <a className={v.primary} style={{ textDecoration: 'none', display: 'inline-block' }} href="/integrations/outlook/connect">{label}</a>
  );

  return (
    <div className={'mail-ui mail-settings ' + v.card} style={style}>
      <Title v={v}>Outlook</Title>

      {!status.connected && (
        <>
          <p className={v.sub}>Connect your Outlook or Microsoft 365 mailbox, then ask about your email right in chat.</p>
          <div className="mail-settings-row">
            <span className="mail-settings-state">Not connected</span>
            {connectButton('Connect')}
          </div>
        </>
      )}

      {status.connected && status.needs_reconnect && (
        <>
          <div className="mail-settings-warning" role="status">
            Microsoft signed this connection out (a password change or expired sign-in does this). Reconnect to keep using your mail in chat.
          </div>
          <div className="mail-settings-row">
            <span className="mail-settings-state">{status.email || 'Outlook account'}</span>
            <span style={{ display: 'flex', gap: 8 }}>
              <button className={v.secondary} onClick={disconnect}>Disconnect</button>
              {connectButton('Reconnect')}
            </span>
          </div>
        </>
      )}

      {status.connected && !status.needs_reconnect && (
        <>
          <div className="mail-settings-row">
            <span className="mail-settings-state">
              <span className="mail-settings-dot" aria-hidden="true" />
              {status.email || 'Connected'}
              {status.connected_at && (
                <span className="mail-settings-since"> · since {new Date(status.connected_at).toLocaleDateString()}</span>
              )}
            </span>
            <button className={v.secondary} onClick={disconnect}>Disconnect</button>
          </div>
          <div className="mail-settings-examples">
            <div className="mail-settings-heading">Try asking in chat</div>
            <ul>{OUTLOOK_EXAMPLE_PROMPTS.map((p) => <li key={p}>“{p}”</li>)}</ul>
          </div>
        </>
      )}

      <ul className="mail-settings-facts">
        <li><strong>Read-only.</strong> The assistant can read and search your mail, but can never send, delete, move or change anything.</li>
        <li>
          <strong>How your data is used.</strong> Emails you ask about are sent to our AI provider to write the answer. The
          answer, plus the sender, subject and date of emails it cites, is saved in your chat history.
        </li>
        {status.connected && (
          <li>
            <strong>Fully revoke access.</strong> Disconnect deletes our saved sign-in. To also remove the app from your Microsoft
            account:{' '}
            {MICROSOFT_REVOKE_LINKS.map((l, i) => (
              <span key={l.href}>
                {i > 0 && ' · '}
                <a href={l.href} target="_blank" rel="noopener noreferrer">{l.label}</a>
              </span>
            ))}
          </li>
        )}
      </ul>
    </div>
  );
}

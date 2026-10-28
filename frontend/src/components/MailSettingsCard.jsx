import { post } from '../api.js';
import { confirmDialog, showToast } from '../lib/notify.js';
import { useMailStatus, refreshMailStatus, MAIL_PROVIDERS, MAIL_EXAMPLE_PROMPTS } from '../lib/mail.js';
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

/// One linked mailbox (provider = 'outlook' | 'gmail') -- hidden entirely
/// when that provider's flag is off server-side. Connect is a full-page
/// navigation (not fetch): the backend redirects to Microsoft/Google, which
/// redirects back to /app with a result App.jsx toasts.
export default function MailSettingsCard({ provider, variant = 'classic', style }) {
  const v = VARIANTS[variant];
  const p = MAIL_PROVIDERS[provider];
  const all = useMailStatus();
  const status = all?.[provider];
  if (!status?.enabled) return null;

  const connectHref = `/integrations/${provider}/connect`;

  const disconnect = async () => {
    const ok = await confirmDialog({
      title: `Disconnect ${p.label}?`,
      message: `The assistant will stop reading your ${p.label} mail, and the saved sign-in is deleted. You can reconnect any time.`,
      confirmLabel: 'Disconnect',
      danger: true,
    });
    if (!ok) return;
    try {
      await post(`/integrations/${provider}/disconnect`, {});
      await refreshMailStatus();
      showToast(`${p.label} disconnected`);
    } catch (e) {
      showToast('Could not disconnect: ' + e.message, 'error');
    }
  };

  const connectButton = (label) => (
    <a className={v.primary} style={{ textDecoration: 'none', display: 'inline-block' }} href={connectHref}>{label}</a>
  );

  return (
    <div className={'mail-ui mail-settings ' + v.card} style={style}>
      <Title v={v}>{p.label}</Title>

      {!status.connected && (
        <>
          <p className={v.sub}>{p.blurb}</p>
          <div className="mail-settings-row">
            <span className="mail-settings-state">Not connected</span>
            {connectButton('Connect')}
          </div>
        </>
      )}

      {status.connected && status.needs_reconnect && (
        <>
          <div className="mail-settings-warning" role="status">
            {p.signedOutReason} Reconnect to keep using your mail in chat.
          </div>
          <div className="mail-settings-row">
            <span className="mail-settings-state">{status.email || `${p.label} account`}</span>
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
            <ul>{MAIL_EXAMPLE_PROMPTS.map((q) => <li key={q}>“{q}”</li>)}</ul>
          </div>
        </>
      )}

      <ul className="mail-settings-facts">
        <li><strong>Read-only.</strong> The assistant can read and search your mail, but can never send, delete, move or change anything.</li>
        <li>
          <strong>How your data is used.</strong> Emails you ask about are sent to our AI provider to write the answer. The
          answer, plus the sender, subject and date of emails it cites, is saved in your chat history.
        </li>
        {status.weekly_reconnect && (
          <li>
            <strong>Weekly reconnect, for now.</strong> While Google is still verifying our access, Google signs this
            connection out after 7 days -- you'll see a Reconnect button when that happens.
          </li>
        )}
        {status.connected && (
          <li>
            <strong>Fully revoke access.</strong> Disconnect deletes our saved sign-in. To also remove the app from your
            account:{' '}
            {p.revokeLinks.map((l, i) => (
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

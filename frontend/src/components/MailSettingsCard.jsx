import { post } from '../api.js';
import { confirmDialog, showToast } from '../lib/notify.js';
import {
  useMailStatus, refreshMailStatus, mailAccounts, connectHref, MAIL_PROVIDERS, MAIL_EXAMPLE_PROMPTS,
} from '../lib/mail.js';
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

/// One provider's linked mailboxes (provider = 'outlook' | 'gmail') -- a
/// user may link several (personal + work). Hidden entirely when that
/// provider's flag is off server-side. Connect/Add/Reconnect are full-page
/// navigations (not fetch): the backend redirects to Microsoft/Google, which
/// redirects back to /app with a result App.jsx toasts.
export default function MailSettingsCard({ provider, variant = 'classic', style }) {
  const v = VARIANTS[variant];
  const p = MAIL_PROVIDERS[provider];
  const all = useMailStatus();
  const status = all?.[provider];
  if (!status?.enabled) return null;
  const accounts = mailAccounts(status);
  const anyBroken = accounts.some((a) => a.needs_reconnect);

  const disconnect = async (account) => {
    const which = account.email || `this ${p.label} account`;
    const ok = await confirmDialog({
      title: `Disconnect ${which}?`,
      message: `The assistant will stop reading ${which}, and its saved sign-in is deleted. Other linked mailboxes aren't affected. You can reconnect any time.`,
      confirmLabel: 'Disconnect',
      danger: true,
    });
    if (!ok) return;
    try {
      await post(`/integrations/${provider}/disconnect`, account.id != null ? { account_id: account.id } : {});
      await refreshMailStatus();
      showToast(`${which} disconnected`);
    } catch (e) {
      showToast('Could not disconnect: ' + e.message, 'error');
    }
  };

  const linkButton = (label, href, kind = 'primary') => (
    <a className={v[kind]} style={{ textDecoration: 'none', display: 'inline-block' }} href={href}>{label}</a>
  );

  return (
    <div className={'mail-ui mail-settings ' + v.card} style={style}>
      <Title v={v}>{p.label}</Title>

      {accounts.length === 0 ? (
        <>
          <p className={v.sub}>{p.blurb}</p>
          <div className="mail-settings-row">
            <span className="mail-settings-state">Not connected</span>
            {linkButton('Connect', connectHref(provider))}
          </div>
        </>
      ) : (
        <>
          {anyBroken && (
            <div className="mail-settings-warning" role="status">
              {p.signedOutReason} Reconnect the marked mailbox to keep using it in chat.
            </div>
          )}
          <ul className="mail-accounts" aria-label={`Linked ${p.label} accounts`}>
            {accounts.map((a) => (
              <li key={a.id ?? a.email} className="mail-settings-row">
                <span className="mail-settings-state">
                  <span className={'mail-settings-dot' + (a.needs_reconnect ? ' warn' : '')} aria-hidden="true" />
                  <span>
                    {a.email || `${p.label} account`}
                    <span className="mail-settings-since">
                      {a.needs_reconnect ? ' · needs reconnecting'
                        : a.connected_at ? ` · since ${new Date(a.connected_at).toLocaleDateString()}` : ''}
                    </span>
                  </span>
                </span>
                <span style={{ display: 'flex', gap: 8 }}>
                  {a.needs_reconnect && linkButton('Reconnect', connectHref(provider, a.email))}
                  <button className={v.secondary} onClick={() => disconnect(a)}>Disconnect</button>
                </span>
              </li>
            ))}
          </ul>
          <a className="mail-add-account" href={connectHref(provider)}>+ Add another {p.label} account</a>
          {!anyBroken && (
            <div className="mail-settings-examples">
              <div className="mail-settings-heading">Try asking in chat</div>
              <ul>{MAIL_EXAMPLE_PROMPTS.map((q) => <li key={q}>“{q}”</li>)}</ul>
              {accounts.length > 1 && (
                <p className="mail-settings-hint">
                  Questions cover all your {p.label} accounts -- name one (“in my work inbox…”) to narrow it down.
                </p>
              )}
            </div>
          )}
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
            <strong>Weekly reconnect, for now.</strong> While Google is still verifying our access, Google signs each
            connection out after 7 days -- you'll see a Reconnect button when that happens.
          </li>
        )}
        {accounts.length > 0 && (
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

import { useMailStatus, anyMailReady, MAIL_EXAMPLE_PROMPTS } from '../lib/mail.js';
import '../styles/mail.css';

/// "Try with your inbox" chips for the global chat -- only once a mailbox
/// (Outlook or Gmail) is connected and usable, so nobody is offered
/// something that can't work. The caller decides when to stop showing them
/// (see hasMailReply below).
export default function MailSuggestions({ onPick, disabled, style }) {
  const all = useMailStatus();
  if (!anyMailReady(all)) return null;
  return (
    <div className="mail-ui mail-suggestions" style={style}>
      <span className="mail-suggestions-label">📧 Try with your inbox:</span>
      {MAIL_EXAMPLE_PROMPTS.map((p) => (
        <button key={p} type="button" className="mail-suggestion" disabled={disabled} onClick={() => onPick(p)}>
          {p}
        </button>
      ))}
    </div>
  );
}

/** True once the thread already has a reply that used mail -- the user has
 * discovered the feature, so the suggestion chips can step aside. */
export function hasMailReply(messages) {
  return (messages || []).some((m) => m.replyMeta || m.reply_meta);
}

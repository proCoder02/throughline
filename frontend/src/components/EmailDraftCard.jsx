import { useRef, useState } from 'react';
import { post, del } from '../api.js';
import { uploadFile, confirmUpload } from '../lib/uploads.js';
import { DraftIcon, AttachIcon, CloseIcon } from '../icons.jsx';

// Web counterpart of the Flutter client's EmailDraftCard (email_draft_card.dart)
// -- the "review before sending" card a global-chat reply carries as
// action_cards when send_email's recipient isn't a known friend, or
// file_vehicle_insurance_claim drafted a claim email (see app.py's
// _tool_send_email/_tool_file_vehicle_insurance_claim). The email is already
// composed and held as a pending row server-side; Send/Cancel here is the
// only thing that decides whether it actually goes out.
export default function EmailDraftCard({ card, onResolved }) {
  // 'pending' | 'busy' | 'sent' | 'dismissed' | 'error'
  const [state, setState] = useState(card._resolvedStatus || 'pending');
  const [attachments, setAttachments] = useState(card._attachments || []);
  const [attaching, setAttaching] = useState(false);
  const [errorText, setErrorText] = useState(null);
  const fileInputRef = useRef(null);

  const draftId = card.draft_id;

  const send = async () => {
    setState('busy');
    try {
      await post(`/email-drafts/${draftId}/confirm`, {});
      card._resolvedStatus = 'sent';
      setState('sent');
      onResolved?.();
    } catch (e) {
      setErrorText('Could not send this email.');
      setState('error');
    }
  };

  const cancel = async () => {
    setState('busy');
    try {
      await post(`/email-drafts/${draftId}/dismiss`, {});
    } catch (e) {
      // Dismiss failing silently is fine -- worst case the card stays
      // pending server-side; it can never accidentally send either way.
    }
    card._resolvedStatus = 'dismissed';
    setState('dismissed');
    onResolved?.();
  };

  const attach = async (file) => {
    if (!file) return;
    setAttaching(true);
    try {
      const contentType = file.type || 'application/octet-stream';
      const objectKey = await uploadFile(file, 'chat_file');
      await confirmUpload(objectKey, 'chat_file');
      const data = await post(`/email-drafts/${draftId}/attachments`, {
        object_key: objectKey, filename: file.name, content_type: contentType,
      });
      card._attachments = data.attachments;
      setAttachments(data.attachments);
    } catch (e) {
      setErrorText(null); // don't knock the whole card into an error state over an attach failure
    } finally {
      setAttaching(false);
    }
  };

  const removeAttachment = async (attachmentId) => {
    setAttaching(true);
    try {
      const data = await del(`/email-drafts/${draftId}/attachments/${attachmentId}`);
      card._attachments = data.attachments;
      setAttachments(data.attachments);
    } catch (e) {
      // Leaving the chip in place on failure is fine -- the user can just
      // try removing it again, same as every other best-effort action here.
    } finally {
      setAttaching(false);
    }
  };

  if (state === 'sent') {
    return (
      <div className="action-card">
        <div className="action-card-done"><DraftIcon /> Email sent to {card.to_email}.</div>
      </div>
    );
  }
  if (state === 'dismissed') {
    return <div className="action-card"><div className="action-card-done">Draft cancelled.</div></div>;
  }
  if (state === 'error') {
    return <div className="action-card"><div className="action-card-done">{errorText || 'Something went wrong.'}</div></div>;
  }

  const busy = state === 'busy' || attaching;

  return (
    <div className="action-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, fontSize: 13 }}>
        <DraftIcon /> To: {card.to_email}
      </div>
      <div style={{ marginTop: 6, fontWeight: 600, fontSize: 13.5 }}>{card.subject}</div>
      <div
        className="action-card-need"
        style={{
          marginTop: 4, maxHeight: '8em', overflow: 'hidden', whiteSpace: 'pre-wrap',
          display: '-webkit-box', WebkitLineClamp: 6, WebkitBoxOrient: 'vertical',
        }}
      >
        {card.body}
      </div>
      {attachments.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {attachments.map((a) => (
            <span
              key={a.id}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 6px 4px 10px',
                borderRadius: 999, background: 'var(--wa-hover)', fontSize: 11.5,
              }}
            >
              {a.filename}
              <button
                type="button" disabled={busy} onClick={() => removeAttachment(a.id)}
                className="topic-tag-x"
                aria-label={`Remove ${a.filename}`}
              >
                <CloseIcon />
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        ref={fileInputRef} type="file" style={{ display: 'none' }}
        onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; attach(f); }}
      />
      <button
        type="button" disabled={busy} onClick={() => fileInputRef.current.click()}
        className="btn secondary small"
        style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 8, alignSelf: 'flex-start' }}
      >
        <AttachIcon /> {attaching ? 'Attaching...' : 'Attach file'}
      </button>
      <div className="action-card-buttons" style={{ marginTop: 6 }}>
        <button className="btn" disabled={busy} onClick={send}>Send</button>
        <button className="btn secondary" disabled={busy} onClick={cancel}>Cancel</button>
      </div>
    </div>
  );
}

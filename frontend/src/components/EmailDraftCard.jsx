import { useEffect, useRef, useState } from 'react';
import { apiJson, post, del } from '../api.js';
import { uploadFile, confirmUpload } from '../lib/uploads.js';
import { DraftIcon, AttachIcon, CloseIcon } from '../icons.jsx';

// Web counterpart of the Flutter client's EmailDraftCard (email_draft_card.dart)
// -- the "review before sending" card a global-chat reply carries as
// action_cards when send_email's recipient isn't a known friend, or
// file_vehicle_insurance_claim drafted a claim email (see app.py's
// _tool_send_email/_tool_file_vehicle_insurance_claim). The email is already
// composed and held as a pending row server-side.
//
// Every state this card shows comes from the server: it loads the email's
// real status when it appears (it may have been sent from the chat, another
// device, or before a reload), and Send / Cancel / Attach only change the
// card when the server says the action happened. A failure keeps the draft
// pending with the server's reason, so it can be retried.
export default function EmailDraftCard({ card, onResolved }) {
  // 'pending' | 'busy' | 'sent' | 'sending' | 'dismissed'
  const [state, setState] = useState(card._resolvedStatus || card.status || 'pending');
  const [record, setRecord] = useState(card._record || null);
  const [attachments, setAttachments] = useState(card._attachments || []);
  const [attaching, setAttaching] = useState(false);
  const [errorText, setErrorText] = useState(null);
  const fileInputRef = useRef(null);

  const draftId = card.draft_id;

  const apply = (data) => {
    if (!data || !data.status) return;
    card._resolvedStatus = data.status;
    card._record = data;
    setRecord(data);
    setState(data.status);
    if (Array.isArray(data.attachments)) {
      card._attachments = data.attachments;
      setAttachments(data.attachments);
    }
  };

  useEffect(() => {
    let alive = true;
    apiJson(`/email-drafts/${draftId}`)
      .then((data) => { if (alive) apply(data); })
      .catch(() => { /* keep what the chat reply said; the next action re-checks anyway */ });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId]);

  const failed = (e, fallback) => {
    if (e?.status === 409 && e.data?.status) {
      apply(e.data); // already sent / cancelled elsewhere: show that, not an error
      setErrorText(null);
      return;
    }
    setState('pending');
    setErrorText(e?.data?.error || e?.message || fallback);
  };

  const send = async () => {
    setState('busy');
    setErrorText(null);
    try {
      const data = await post(`/email-drafts/${draftId}/confirm`, {});
      if (!data.ok || data.status !== 'sent') throw Object.assign(new Error(data.error || 'Not sent'), { data });
      apply(data);
      onResolved?.();
    } catch (e) {
      failed(e, 'The email was not sent. Please try again.');
    }
  };

  const cancel = async () => {
    setState('busy');
    setErrorText(null);
    try {
      const data = await post(`/email-drafts/${draftId}/dismiss`, {});
      if (!data.ok || data.status !== 'dismissed') throw Object.assign(new Error(data.error || 'Not cancelled'), { data });
      apply(data);
      onResolved?.();
    } catch (e) {
      failed(e, 'Could not cancel the draft. Please try again.');
    }
  };

  const attach = async (file) => {
    if (!file) return;
    setAttaching(true);
    setErrorText(null);
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
      if (e?.status === 409 && e.data?.status) apply(e.data);
      setErrorText(e?.data?.error || `Could not attach ${file.name}. Please try again.`);
    } finally {
      setAttaching(false);
    }
  };

  const removeAttachment = async (attachmentId) => {
    setAttaching(true);
    setErrorText(null);
    try {
      const data = await del(`/email-drafts/${draftId}/attachments/${attachmentId}`);
      card._attachments = data.attachments;
      setAttachments(data.attachments);
    } catch (e) {
      setErrorText(e?.data?.error || 'Could not remove that file. Please try again.');
    } finally {
      setAttaching(false);
    }
  };

  if (state === 'sent') {
    const verified = record ? record.sent_box_verified : card.sent_box_verified;
    const copyTo = record?.copy_to || card.copy_to;
    return (
      <div className="action-card">
        <div className="action-card-done"><DraftIcon /> Email sent to {card.to_email}.</div>
        <div className="action-card-need" style={{ marginTop: 4 }}>
          {verified
            ? 'Confirmed in Throughline’s Sent folder.'
            : verified === false
              ? 'The mail server accepted it, but it could not be confirmed in Throughline’s Sent folder.'
              : 'Sent before Sent-folder tracking, so it cannot be checked there.'}
          {copyTo ? ` A copy went to your inbox (${copyTo}).` : ''}
        </div>
      </div>
    );
  }
  if (state === 'sending') {
    return <div className="action-card"><div className="action-card-done"><DraftIcon /> Sending to {card.to_email}…</div></div>;
  }
  if (state === 'dismissed') {
    return <div className="action-card"><div className="action-card-done">Draft cancelled. Nothing was sent.</div></div>;
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
      {errorText && (
        <div role="alert" style={{ marginTop: 8, fontSize: 12.5, color: 'var(--danger, #c0392b)' }}>{errorText}</div>
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

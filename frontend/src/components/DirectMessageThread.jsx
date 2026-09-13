import { useEffect, useRef, useState } from 'react';
import Composer from './Composer.jsx';
import MessageRow from './MessageRow.jsx';
import InfoPanel from './InfoPanel.jsx';
import Avatar from './Avatar.jsx';
import PhotoViewer from './PhotoViewer.jsx';
import AttachmentComposePreview from './AttachmentComposePreview.jsx';
import { BackIcon, BrainIcon, CheckIcon, ClockIcon, AlertIcon, CloseIcon, DoubleCheckIcon, InfoIcon } from '../icons.jsx';
import { apiJson, post } from '../api.js';
import { showToast } from '../lib/notify.js';
import { queueOutboxMessage, removeOutboxMessage, getOutboxForFriend } from '../db.js';
import { uploadFile, confirmUpload, makeThumbnailDataUrl, captureVideoFrame, getUploadsStatus } from '../lib/uploads.js';

function kindOf(file) {
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.startsWith('video/')) return 'video';
  return 'file';
}
function purposeOf(kind) {
  return kind === 'image' ? 'chat_image' : kind === 'video' ? 'chat_video' : 'chat_file';
}

// Single tick (sent) -> double grey (delivered, reached their device) ->
// double blue (read, they opened it) -- same three states as the mobile
// client's DirectMessage.tickState. `_status` (sending/failed) only exists
// on a message this tab created locally and hasn't confirmed with the
// server yet -- see the outbox flow below.
function Tick({ message, onRetry }) {
  if (message._status === 'sending') return <span className="dm-tick sending" title="Sending..."><ClockIcon /></span>;
  if (message._status === 'failed') {
    return (
      <button className="dm-tick failed" title="Not sent -- tap to retry" onClick={onRetry}>
        <AlertIcon />
      </button>
    );
  }
  if (message.read_at) return <span className="dm-tick read"><DoubleCheckIcon /></span>;
  if (message.delivered_at) return <span className="dm-tick"><DoubleCheckIcon /></span>;
  return <span className="dm-tick"><CheckIcon /></span>;
}

/// Real-time 1:1 text chat with a friend -- distinct from ChatThread (a solo
/// Listen conversation's LLM Q&A). Delivery/read acks ride the same
/// persistent /ws/notify connection as everything else (see
/// useNotifications' sendDmAck/registerDmListener), not a separate REST
/// call per tick -- mirrors the Flutter client's DirectMessageScreen exactly.
export default function DirectMessageThread({ friend, myUserId, notify, onBack, onViewProfile }) {
  const [messages, setMessages] = useState(null);
  const [infoOpen, setInfoOpen] = useState(false);
  // WhatsApp-style full-screen profile-picture viewer -- opened by clicking
  // the friend's avatar (header or Friend info panel); no-op when they have
  // no real photo (PhotoViewer itself renders nothing without a url).
  const [viewingPhoto, setViewingPhoto] = useState(false);
  const scrollRef = useRef(null);

  // Object storage (Cloudflare R2) attachments -- same
  // null-until-known/hide-if-disabled pattern used in SettingsSection (see
  // GET /uploads/status). pendingAttachment holds a picked-but-not-yet-sent
  // file through its preview step, same shape as ChatThread's pendingImage.
  const [uploadsEnabled, setUploadsEnabled] = useState(false);
  const [pendingAttachment, setPendingAttachment] = useState(null); // {file, kind}
  const [attachmentUploading, setAttachmentUploading] = useState(false);
  const [attachmentProgress, setAttachmentProgress] = useState(0);
  useEffect(() => { getUploadsStatus().then((s) => setUploadsEnabled(!!s.enabled)); }, []);

  // -- Cognitive Sharing (Phase 2/3) --------------------------------------
  const [sharingAvailable, setSharingAvailable] = useState(false); // both sides >= 'limited'
  const [requestingSuggestion, setRequestingSuggestion] = useState(false);
  const [suggestion, setSuggestion] = useState(null);
  const [suggestionClosing, setSuggestionClosing] = useState(false);

  // Event-driven auto-check: counts new messages (sent or received) since
  // the last check, not wall-clock time -- an automatic re-check only ever
  // fires when there's actually new conversation to reason about, so cost
  // scales with real activity rather than a fixed polling interval. The
  // pulse threshold is deliberately lower than the auto-fire one so the
  // button visibly "builds up" before the automatic check happens, rather
  // than a suggestion just appearing with no warning. Mirrors the Flutter
  // client's DirectMessageScreen exactly.
  const PULSE_THRESHOLD = 3;
  const AUTO_CHECK_THRESHOLD = 6;
  const [messagesSinceLastCheck, setMessagesSinceLastCheck] = useState(0);
  const shouldPulse = sharingAvailable && !suggestion && !requestingSuggestion && messagesSinceLastCheck >= PULSE_THRESHOLD;

  const loadLatestSuggestion = () => {
    apiJson(`/friends/${friend.id}/cognitive-suggestion`)
      .then((data) => setSuggestion(data.suggestion || null))
      .catch(() => {});
  };

  useEffect(() => {
    let cancelled = false;
    apiJson(`/friends/${friend.id}/messages`).then(async (items) => {
      if (cancelled) return;
      // Anything still sitting in the outbox from a previous session (tab
      // closed/crashed mid-send, or a reload while offline) belongs at the
      // end of history -- it was composed after everything the server
      // already has. Shown as "failed" immediately; flushOutbox (below)
      // retries them right away rather than waiting for a manual tap.
      const queued = await getOutboxForFriend(friend.id);
      if (cancelled) return;
      setMessages([
        ...items,
        ...queued.map((q) => ({
          id: q.localId, _localId: q.localId, sender_id: myUserId,
          content: q.content, created_at: q.createdAt, _status: 'failed',
          attachment_url: q.attachmentUrl, attachment_type: q.attachmentType, thumbnail_data_url: q.thumbnailDataUrl,
        })),
      ]);
      // Safety-net backfill -- registerDmListener below already acks
      // delivery for every live event while this thread is mounted; this
      // only matters for messages that arrived before that (e.g. the
      // server's pending-event queue itself capped out during a long
      // offline stretch). sendDmAck is idempotent server-side.
      if (items.some((m) => m.sender_id !== myUserId && !m.delivered_at)) {
        notify.sendDmAck({ type: 'ack_delivered', friend_id: friend.id });
      }
      notify.sendDmAck({ type: 'ack_read', friend_id: friend.id });
      notify.clearDmUnread(friend.id);
      flushOutbox();
    });

    apiJson(`/friends/${friend.id}/cognitive-sharing`)
      .then((status) => { if (!cancelled) setSharingAvailable(!!status.both_enabled); })
      .catch(() => {});
    loadLatestSuggestion();

    const unregister = notify.registerDmListener(friend.id, (msg) => {
      if (msg.type === 'direct_message') {
        setMessages((prev) => [...(prev || []), msg.message]);
        // Arrived while this thread was already open -- read immediately.
        notify.sendDmAck({ type: 'ack_read', friend_id: friend.id });
        notify.clearDmUnread(friend.id);
        bumpMessageActivity(1);
      } else if (msg.type === 'direct_messages_read' || msg.type === 'direct_messages_delivered') {
        const field = msg.type === 'direct_messages_read' ? 'read_at' : 'delivered_at';
        const ids = new Set(msg.message_ids);
        const now = new Date().toISOString();
        setMessages((prev) => (prev || []).map((m) => (ids.has(m.id) ? { ...m, [field]: now } : m)));
      } else if (msg.type === 'cognitive_suggestion') {
        // Payload only carries an id (never raw memory, see
        // COGNITIVE_SHARING_INTERVENTION_PLAN.md's guardrails) -- fetch the
        // actual text rather than trusting the WS event alone.
        loadLatestSuggestion();
      }
    });

    return () => {
      cancelled = true;
      unregister();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [friend.id]);

  const findCommonGround = async (silent = false) => {
    if (requestingSuggestion) return;
    setRequestingSuggestion(true);
    try {
      const data = await post(`/friends/${friend.id}/cognitive-suggestion`, {});
      setMessagesSinceLastCheck(0);
      if (data.suggestion) {
        setSuggestion(data.suggestion);
      } else if (!silent) {
        showToast('Nothing to suggest right now.');
      }
    } catch (e) {
      if (!silent) showToast('Could not check: ' + e.message, 'error');
    } finally {
      setRequestingSuggestion(false);
    }
  };

  // Bumps the event-driven counter and silently auto-fires a check once it
  // crosses AUTO_CHECK_THRESHOLD -- "silent" meaning no alert if it turns
  // out there's nothing to suggest, unlike a manual click.
  const bumpMessageActivity = (count) => {
    if (!sharingAvailable || count <= 0 || suggestion) return;
    setMessagesSinceLastCheck((prev) => {
      const next = prev + count;
      if (next >= AUTO_CHECK_THRESHOLD && !requestingSuggestion) findCommonGround(true);
      return next;
    });
  };

  const dismissSuggestion = async () => {
    const current = suggestion;
    if (!current) return;
    setSuggestionClosing(true);
    setTimeout(() => {
      setSuggestion(null);
      setSuggestionClosing(false);
    }, 220); // matches .cognitive-suggestion-card.closing's animation duration
    try {
      await post(`/friends/${friend.id}/cognitive-suggestion/${current.id}/dismiss`, {});
    } catch {
      // Not worth surfacing an error for a dismiss -- worst case it
      // reappears on next load, which is harmless.
    }
  };

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages?.length]);

  // Optimistic send + offline queue: the bubble appears the instant you hit
  // send (before the network round-trip even starts), backed by an
  // IndexedDB outbox entry (see db.js) so a message survives a reload if
  // it fails or the tab closes before the request finishes. Replaced with
  // the real server row (real id, so WS read/delivered acks can match it)
  // the moment the POST actually succeeds.
  const flushingRef = useRef(false);

  // attachment (if any): {attachmentUrl, attachmentType, thumbnailDataUrl} --
  // already uploaded to R2 by the time this runs (see sendAttachment below).
  // The outbox/retry machinery only ever re-attempts this small JSON POST,
  // never the upload itself.
  const attemptSend = async (localId, content, attachment) => {
    try {
      const message = await post(`/friends/${friend.id}/messages`, {
        content,
        attachment_url: attachment?.attachmentUrl,
        attachment_type: attachment?.attachmentType,
        thumbnail_data_url: attachment?.thumbnailDataUrl,
      });
      await removeOutboxMessage(localId);
      setMessages((prev) => (prev || []).map((m) => (m._localId === localId ? message : m)));
      bumpMessageActivity(1);
    } catch (e) {
      setMessages((prev) => (prev || []).map((m) => (m._localId === localId ? { ...m, _status: 'failed' } : m)));
    }
  };

  const flushOutbox = async () => {
    if (flushingRef.current) return;
    flushingRef.current = true;
    try {
      const pending = await getOutboxForFriend(friend.id);
      for (const item of pending) {
        await attemptSend(item.localId, item.content, {
          attachmentUrl: item.attachmentUrl, attachmentType: item.attachmentType, thumbnailDataUrl: item.thumbnailDataUrl,
        });
      }
    } finally {
      flushingRef.current = false;
    }
  };

  // Retries flush automatically the moment the browser thinks connectivity
  // is back -- the user shouldn't have to notice they were offline at all,
  // let alone manually retry every stuck message.
  useEffect(() => {
    window.addEventListener('online', flushOutbox);
    return () => window.removeEventListener('online', flushOutbox);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [friend.id]);

  const send = async (text) => {
    const localId = await queueOutboxMessage(friend.id, text);
    const offline = typeof navigator !== 'undefined' && navigator.onLine === false;
    setMessages((prev) => [...(prev || []), {
      id: localId, _localId: localId, sender_id: myUserId, content: text,
      created_at: new Date().toISOString(), _status: offline ? 'failed' : 'sending',
    }]);
    if (offline) {
      showToast("You're offline -- this will send once you're back online.");
      return;
    }
    attemptSend(localId, text);
  };

  // Picking + previewing an attachment doesn't touch the network at all --
  // only confirming the preview (see AttachmentComposePreview's onSend)
  // triggers the actual upload. Requires being online now: unlike a plain
  // text message, an attachment's bytes can't be queued for a later retry
  // without keeping the whole file in IndexedDB, which is real added
  // complexity for a case (losing connectivity mid-pick) that's rare enough
  // to just ask the user to try again once back online.
  const sendAttachment = async (caption) => {
    const { file, kind } = pendingAttachment;
    setPendingAttachment(null);
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      showToast("You're offline -- try sending this attachment again once you're back online.", 'error');
      return;
    }
    setAttachmentUploading(true);
    setAttachmentProgress(0);
    try {
      const purpose = purposeOf(kind);
      const objectKey = await uploadFile(file, purpose, setAttachmentProgress);
      const attachmentUrl = await confirmUpload(objectKey, purpose);
      let thumbnailDataUrl = null;
      if (kind === 'image') {
        thumbnailDataUrl = await makeThumbnailDataUrl(file).catch(() => null);
      } else if (kind === 'video') {
        thumbnailDataUrl = await captureVideoFrame(file).then((frame) => makeThumbnailDataUrl(frame)).catch(() => null);
      }
      const attachment = { attachmentUrl, attachmentType: file.type, thumbnailDataUrl };
      const localId = await queueOutboxMessage(friend.id, caption, attachment);
      setMessages((prev) => [...(prev || []), {
        id: localId, _localId: localId, sender_id: myUserId, content: caption,
        created_at: new Date().toISOString(), _status: 'sending',
        attachment_url: attachmentUrl, attachment_type: file.type, thumbnail_data_url: thumbnailDataUrl,
      }]);
      attemptSend(localId, caption, attachment);
    } catch (e) {
      showToast('Could not send attachment: ' + e.message, 'error');
    } finally {
      setAttachmentUploading(false);
    }
  };

  const retrySend = (message) => {
    setMessages((prev) => (prev || []).map((m) => (m._localId === message._localId ? { ...m, _status: 'sending' } : m)));
    attemptSend(message._localId, message.content, {
      attachmentUrl: message.attachment_url, attachmentType: message.attachment_type, thumbnailDataUrl: message.thumbnail_data_url,
    });
  };

  // Debounced client-side: only actually sends a 'typing' signal once every
  // 2s while the user keeps typing, not on every keystroke -- the
  // recipient's own indicator already stays up for 3s per signal (see
  // useNotifications' typingFriends timeout), so re-sending more often than
  // that is just wasted traffic. Mirrors the Flutter client's
  // DirectMessageScreen._maybeSendTyping exactly.
  const lastTypingSentRef = useRef(0);
  const maybeSendTyping = () => {
    const now = Date.now();
    if (now - lastTypingSentRef.current < 2000) return;
    lastTypingSentRef.current = now;
    notify.sendTyping(friend.id);
  };

  const isTyping = notify.typingFriends.has(friend.id);

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <button className="back-btn" title="Back" aria-label="Back to friends list" onClick={onBack}><BackIcon /></button>
        {friend.profile_picture_url ? (
          <button
            className="avatar-view-btn" title="View profile picture" aria-label="View profile picture"
            onClick={() => setViewingPhoto(true)}
          >
            <Avatar url={friend.profile_picture_url} name={friend.nickname || friend.username} />
          </button>
        ) : (
          <Avatar url={friend.profile_picture_url} name={friend.nickname || friend.username} />
        )}
        <div className="chat-header-info">
          <div className="chat-header-name">{friend.nickname || friend.username}</div>
          {isTyping && (
            <div className="chat-header-sub typing-indicator" role="status" aria-live="polite">
              <span className="typing-dots" aria-hidden="true"><span /><span /><span /></span>
              <span className="sr-only">{(friend.nickname || friend.username) + ' is typing'}</span>
            </div>
          )}
        </div>
        <div className="chat-header-actions">
          {sharingAvailable && (
            <button
              className={shouldPulse ? 'pulse' : ''}
              title="Find common ground" aria-label="Find common ground"
              disabled={requestingSuggestion}
              onClick={() => findCommonGround()}
            >
              <BrainIcon />
            </button>
          )}
          <button
            title="Friend info" aria-label="Friend info" aria-pressed={infoOpen}
            className={infoOpen ? 'active' : ''} onClick={() => setInfoOpen((v) => !v)}
          >
            <InfoIcon />
          </button>
        </div>
      </div>

      {suggestion && (
        <div className={'cognitive-suggestion-card' + (suggestionClosing ? ' closing' : '')}>
          <BrainIcon />
          <span className="cognitive-suggestion-text">{suggestion.suggestion_text}</span>
          <button title="Dismiss" aria-label="Dismiss suggestion" onClick={dismissSuggestion}><CloseIcon /></button>
        </div>
      )}

      <div className="messages" ref={scrollRef}>
        {messages === null ? null : messages.length === 0 ? (
          <div className="list-empty">Say hello to {friend.nickname || friend.username}</div>
        ) : (
          messages.map((m) => {
            const mine = m.sender_id === myUserId;
            return (
              <MessageRow
                key={m.id}
                mine={mine}
                timestamp={m.created_at}
                content={m.content}
                attachmentUrl={m.attachment_url}
                attachmentType={m.attachment_type}
                thumbnailDataUrl={m.thumbnail_data_url}
                attachmentSummary={m.attachment_summary}
                trailingIcon={mine ? <Tick message={m} onRetry={() => retrySend(m)} /> : null}
              />
            );
          })
        )}
      </div>

      {pendingAttachment ? (
        <AttachmentComposePreview
          file={pendingAttachment.file} kind={pendingAttachment.kind}
          uploading={attachmentUploading} progress={attachmentProgress}
          onCancel={() => setPendingAttachment(null)}
          onSend={sendAttachment}
        />
      ) : (
        <Composer
          onSend={send} placeholder="Message" onTyping={maybeSendTyping}
          onImageSelected={uploadsEnabled ? (file) => setPendingAttachment({ file, kind: kindOf(file) }) : undefined}
          attachAccept="image/*,video/*,.pdf,.doc,.docx,.txt,.zip"
          attachLabel="Attach a photo, video, or file"
        />
      )}

      <InfoPanel open={infoOpen} onClose={() => setInfoOpen(false)} title="Friend info">
        <div className="info-panel-hero">
          <div className="info-panel-hero-banner" />
          {friend.profile_picture_url ? (
            <button
              className="avatar-view-btn" title="View profile picture" aria-label="View profile picture"
              onClick={() => setViewingPhoto(true)}
            >
              <Avatar url={friend.profile_picture_url} name={friend.nickname || friend.username} size="xl" className="info-panel-hero-avatar" />
            </button>
          ) : (
            <Avatar url={friend.profile_picture_url} name={friend.nickname || friend.username} size="xl" className="info-panel-hero-avatar" />
          )}
          <div className="info-panel-hero-body">
            <div className="info-panel-hero-name">{friend.nickname || friend.username}</div>
            {friend.nickname && <div className="info-panel-hero-sub">{friend.username}</div>}
          </div>
        </div>

        {friend.friends_since && (
          <div className="profile-section" style={{ borderTop: 'none', marginTop: 0, paddingTop: 0 }}>
            <div className="profile-section-title">Friends Since</div>
            <div className="hint" style={{ marginTop: 0 }}>
              {new Date(friend.friends_since).toLocaleDateString([], { year: 'numeric', month: 'long', day: 'numeric' })}
            </div>
          </div>
        )}

        <div className="profile-section">
          <div className="profile-section-title">Cognitive Sharing</div>
          <div className="hint" style={{ marginTop: 0 }}>
            {sharingAvailable ? 'Enabled on both sides -- suggestions can appear.' : 'Not enabled on both sides yet.'}
          </div>
        </div>
        {onViewProfile && (
          <button className="btn secondary" style={{ width: '100%', marginTop: 8 }} onClick={onViewProfile}>
            View full profile
          </button>
        )}
      </InfoPanel>

      {viewingPhoto && (
        <PhotoViewer
          url={friend.profile_picture_url} name={friend.nickname || friend.username}
          onClose={() => setViewingPhoto(false)}
        />
      )}
    </div>
  );
}

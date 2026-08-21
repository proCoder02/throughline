import { useEffect, useRef, useState } from 'react';
import Composer from './Composer.jsx';
import { BackIcon, BrainIcon, CheckIcon, CloseIcon, DoubleCheckIcon } from '../icons.jsx';
import { apiJson, post } from '../api.js';

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// Single tick (sent) -> double grey (delivered, reached their device) ->
// double blue (read, they opened it) -- same three states as the mobile
// client's DirectMessage.tickState.
function Tick({ message }) {
  if (message.read_at) return <span className="dm-tick read"><DoubleCheckIcon /></span>;
  if (message.delivered_at) return <span className="dm-tick"><DoubleCheckIcon /></span>;
  return <span className="dm-tick"><CheckIcon /></span>;
}

/// Real-time 1:1 text chat with a friend -- distinct from ChatThread (a solo
/// Listen conversation's LLM Q&A). Delivery/read acks ride the same
/// persistent /ws/notify connection as everything else (see
/// useNotifications' sendDmAck/registerDmListener), not a separate REST
/// call per tick -- mirrors the Flutter client's DirectMessageScreen exactly.
export default function DirectMessageThread({ friend, myUserId, notify, onBack }) {
  const [messages, setMessages] = useState(null);
  const [sending, setSending] = useState(false);
  const scrollRef = useRef(null);

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
    apiJson(`/friends/${friend.id}/messages`).then((items) => {
      if (cancelled) return;
      setMessages(items);
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
        alert('Nothing to suggest right now.');
      }
    } catch (e) {
      if (!silent) alert('Could not check: ' + e.message);
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

  const send = async (text) => {
    setSending(true);
    try {
      const message = await post(`/friends/${friend.id}/messages`, { content: text });
      setMessages((prev) => [...(prev || []), message]);
      bumpMessageActivity(1);
    } catch (e) {
      alert('Could not send: ' + e.message);
    } finally {
      setSending(false);
    }
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
        <button className="back-btn" title="Back" onClick={onBack}><BackIcon /></button>
        <span className="avatar">{(friend.nickname || friend.username)[0]}</span>
        <div className="chat-header-info">
          <div className="chat-header-name">{friend.nickname || friend.username}</div>
          {isTyping && <div className="chat-header-sub typing-indicator">typing...</div>}
        </div>
        {sharingAvailable && (
          <div className="chat-header-actions">
            <button
              className={shouldPulse ? 'pulse' : ''}
              title="Find common ground"
              disabled={requestingSuggestion}
              onClick={() => findCommonGround()}
            >
              <BrainIcon />
            </button>
          </div>
        )}
      </div>

      {suggestion && (
        <div className={'cognitive-suggestion-card' + (suggestionClosing ? ' closing' : '')}>
          <BrainIcon />
          <span className="cognitive-suggestion-text">{suggestion.suggestion_text}</span>
          <button title="Dismiss" onClick={dismissSuggestion}><CloseIcon /></button>
        </div>
      )}

      <div className="messages" ref={scrollRef}>
        {messages === null ? null : messages.length === 0 ? (
          <div className="list-empty">Say hello to {friend.nickname || friend.username}</div>
        ) : (
          messages.map((m) => {
            const mine = m.sender_id === myUserId;
            return (
              <div className={'bubble-row ' + (mine ? 'out' : 'in')} key={m.id}>
                <div className={'bubble ' + (mine ? 'out' : 'in')}>
                  {m.content}
                  <span className="bubble-time">
                    {formatTime(m.created_at)}
                    {mine && <Tick message={m} />}
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>

      <Composer onSend={send} disabled={sending} placeholder="Message" onTyping={maybeSendTyping} />
    </div>
  );
}

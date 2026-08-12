import { useEffect, useRef, useState } from 'react';
import Composer from './Composer.jsx';
import { BackIcon, CheckIcon, DoubleCheckIcon } from '../icons.jsx';
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

    const unregister = notify.registerDmListener(friend.id, (msg) => {
      if (msg.type === 'direct_message') {
        setMessages((prev) => [...(prev || []), msg.message]);
        // Arrived while this thread was already open -- read immediately.
        notify.sendDmAck({ type: 'ack_read', friend_id: friend.id });
        notify.clearDmUnread(friend.id);
      } else if (msg.type === 'direct_messages_read' || msg.type === 'direct_messages_delivered') {
        const field = msg.type === 'direct_messages_read' ? 'read_at' : 'delivered_at';
        const ids = new Set(msg.message_ids);
        const now = new Date().toISOString();
        setMessages((prev) => (prev || []).map((m) => (ids.has(m.id) ? { ...m, [field]: now } : m)));
      }
    });

    return () => {
      cancelled = true;
      unregister();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [friend.id]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages?.length]);

  const send = async (text) => {
    setSending(true);
    try {
      const message = await post(`/friends/${friend.id}/messages`, { content: text });
      setMessages((prev) => [...(prev || []), message]);
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
      </div>

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

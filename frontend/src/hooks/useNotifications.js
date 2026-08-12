import { useEffect, useRef, useState } from 'react';
import { getToken } from '../api.js';

// Connects once (from App, not per-section, so it survives section
// switches) and turns pushed events into badge state. The server holds a
// pending queue per user, so an event that arrived while this was
// disconnected still shows up the moment it reconnects.
export function useNotifications(enabled) {
  const [taskCount, setTaskCount] = useState(0);
  const [unreadChatIds, setUnreadChatIds] = useState(() => new Set());
  const [incomingCall, setIncomingCall] = useState(null); // {callId, roomName, callerId, callerName}
  const [declinedCallId, setDeclinedCallId] = useState(null); // set when every invitee has declined/left a ringing call
  const [dmUnreadCounts, setDmUnreadCounts] = useState({}); // friendId -> unread count, for the Friends-list badge
  const [typingFriends, setTypingFriends] = useState(() => new Set()); // friendIds currently showing "typing..."
  const socketRef = useRef(null);
  const typingTimersRef = useRef(new Map()); // friendId -> auto-clear timeout id
  // Client -> server sends made before the socket finished its handshake --
  // found via a real bug: DirectMessageThread fires its initial ack_read/
  // ack_delivered from a mount effect, which can genuinely race ahead of
  // the WebSocket reaching OPEN (unlike an ack triggered by an already-
  // arrived 'direct_message' event, which by definition means the socket
  // is already open). Without this queue that first ack was silently
  // dropped with no retry anywhere -- exactly the "ticks never update"
  // symptom this exists to fix.
  const outboxRef = useRef([]);
  // friendId -> handler(msg), registered by whichever DirectMessageThread is
  // currently mounted (see registerDmListener) -- a live event for a friend
  // with no registered listener falls back to just bumping the badge count,
  // matching the mobile client's "active thread" suppression logic.
  const dmListenersRef = useRef(new Map());

  useEffect(() => {
    if (!enabled) return;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getToken();
    const url = `${proto}//${window.location.host}/ws/notify` + (token ? `?token=${encodeURIComponent(token)}` : '');
    const socket = new WebSocket(url);
    socketRef.current = socket;
    socket.onopen = () => {
      const pending = outboxRef.current;
      outboxRef.current = [];
      for (const obj of pending) sendNow(obj);
    };
    socket.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === 'task_created') {
        setTaskCount((c) => c + 1);
      } else if (msg.type === 'chat_message' || msg.type === 'call_conversation_ready') {
        // A call's transcribed conversation shows up in Chats the exact
        // same way a new chat message would -- an unread dot, discovered
        // on next visit to the tab (ChatsSection refetches on mount).
        setUnreadChatIds((prev) => new Set(prev).add(msg.conversation_id));
      } else if (msg.type === 'incoming_call') {
        setIncomingCall({ callId: msg.call_id, roomName: msg.room_name, callerId: msg.caller_id, callerName: msg.caller_name });
      } else if (msg.type === 'call_declined') {
        // Nobody LiveKit-disconnects a caller who's alone in a room nobody
        // else ever joined, so the server tells us explicitly once every
        // invitee has declined -- lets the caller's UI hang up on its own
        // instead of sitting on "Call in progress" forever.
        setDeclinedCallId(msg.call_id);
      } else if (msg.type === 'direct_message') {
        const senderId = msg.message.sender_id;
        // "Reached this device" fires for every incoming message regardless
        // of whether its thread is open right now -- same WhatsApp-style ack
        // model as the mobile client (see NotifyProvider.ackDelivered there).
        sendMessage({ type: 'ack_delivered', friend_id: senderId });
        const handler = dmListenersRef.current.get(senderId);
        if (handler) {
          handler(msg);
        } else {
          setDmUnreadCounts((prev) => ({ ...prev, [senderId]: (prev[senderId] || 0) + 1 }));
        }
      } else if (msg.type === 'direct_messages_read' || msg.type === 'direct_messages_delivered') {
        const handler = dmListenersRef.current.get(msg.friend_id);
        if (handler) handler(msg);
      } else if (msg.type === 'friend_typing') {
        const friendId = msg.friend_id;
        clearTimeout(typingTimersRef.current.get(friendId));
        typingTimersRef.current.set(friendId, setTimeout(() => {
          typingTimersRef.current.delete(friendId);
          setTypingFriends((prev) => {
            if (!prev.has(friendId)) return prev;
            const next = new Set(prev);
            next.delete(friendId);
            return next;
          });
        }, 3000));
        setTypingFriends((prev) => (prev.has(friendId) ? prev : new Set(prev).add(friendId)));
      }
    };
    return () => socket.close();
  }, [enabled]);

  const clearTasks = () => setTaskCount(0);
  const clearChat = (id) => setUnreadChatIds((prev) => {
    if (!prev.has(id)) return prev;
    const next = new Set(prev);
    next.delete(id);
    return next;
  });

  const clearIncomingCall = () => setIncomingCall(null);
  const clearDeclinedCall = () => setDeclinedCallId(null);

  const sendNow = (obj) => {
    try {
      socketRef.current?.send(JSON.stringify(obj));
    } catch { /* best-effort -- a failure here (vs. simply not being open yet, handled by the queue below) genuinely has nowhere better to go */ }
  };

  // Client -> server messages (direct-message delivery/read acks, typing
  // signals) -- rides this same persistent connection rather than a
  // separate REST call per ack, mirroring app.py's
  // _handle_notify_client_message. Queued (capped, oldest dropped first) if
  // the socket isn't OPEN yet, flushed the instant it is (see onopen above)
  // -- see the outboxRef doc comment for why this isn't just best-effort.
  const sendMessage = (obj) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      sendNow(obj);
    } else {
      outboxRef.current.push(obj);
      if (outboxRef.current.length > 20) outboxRef.current.shift();
    }
  };

  // Called by DirectMessageThread's mount/unmount effect. Only one thread
  // is ever open at a time in this UI, so registering implicitly makes this
  // friend "active" (suppressing the badge-only fallback above) for as long
  // as it stays registered.
  const registerDmListener = (friendId, handler) => {
    dmListenersRef.current.set(friendId, handler);
    return () => {
      if (dmListenersRef.current.get(friendId) === handler) dmListenersRef.current.delete(friendId);
    };
  };

  const clearDmUnread = (friendId) => setDmUnreadCounts((prev) => {
    if (!(friendId in prev)) return prev;
    const next = { ...prev };
    delete next[friendId];
    return next;
  });

  const seedDmUnreadCounts = (counts) => setDmUnreadCounts(counts);

  // Debouncing is the caller's job (see DirectMessageThread), same as the
  // mobile client's NotifyProvider.sendTyping -- this just relays whatever
  // it's told to.
  const sendTyping = (friendId) => sendMessage({ type: 'typing', friend_id: friendId });

  return {
    taskCount, unreadChatIds, clearTasks, clearChat, incomingCall, clearIncomingCall, declinedCallId, clearDeclinedCall,
    dmUnreadCounts, registerDmListener, sendDmAck: sendMessage, clearDmUnread, seedDmUnreadCounts,
    typingFriends, sendTyping,
  };
}

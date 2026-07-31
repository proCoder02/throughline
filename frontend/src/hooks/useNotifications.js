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
  const socketRef = useRef(null);

  useEffect(() => {
    if (!enabled) return;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getToken();
    const url = `${proto}//${window.location.host}/ws/notify` + (token ? `?token=${encodeURIComponent(token)}` : '');
    const socket = new WebSocket(url);
    socketRef.current = socket;
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

  return { taskCount, unreadChatIds, clearTasks, clearChat, incomingCall, clearIncomingCall };
}

import { openDB } from 'idb';

// Read-through cache for local search: Postgres stays the source of truth.
// `conversations` holds list metadata + a lowercased searchText blob
// (title + transcript + chat, backfilled lazily as chats are opened).
//
// `dm_outbox` (v2) is the offline-send queue for direct messages -- a
// message that fails to POST (offline, dropped connection) survives a
// reload here instead of silently vanishing, and gets retried the moment
// connectivity returns (see DirectMessageThread's flushOutbox).
const dbPromise = openDB('throughline', 2, {
  upgrade(db, oldVersion) {
    if (oldVersion < 1) db.createObjectStore('conversations', { keyPath: 'id' });
    if (oldVersion < 2) db.createObjectStore('dm_outbox', { keyPath: 'localId' });
  },
});

export async function upsertConversationMeta(conv) {
  const db = await dbPromise;
  const existing = await db.get('conversations', conv.id);
  await db.put('conversations', {
    ...existing,
    id: conv.id,
    title: conv.title,
    created_at: conv.created_at,
    searchText: buildSearchText(conv.title, existing?.transcript, existing?.chatText),
  });
}

export async function backfillConversationContent(id, transcript, chatMessages) {
  const db = await dbPromise;
  const existing = await db.get('conversations', id);
  if (!existing) return;
  const chatText = (chatMessages || []).map((m) => m.content).join(' ');
  await db.put('conversations', {
    ...existing,
    transcript,
    chatText,
    searchText: buildSearchText(existing.title, transcript, chatText),
  });
}

export async function removeConversation(id) {
  const db = await dbPromise;
  await db.delete('conversations', id);
}

export async function searchConversations(query) {
  const db = await dbPromise;
  const all = await db.getAll('conversations');
  const q = query.trim().toLowerCase();
  if (!q) return all;
  return all.filter((c) => (c.searchText || '').includes(q));
}

function buildSearchText(title, transcript, chatText) {
  return [title, transcript, chatText].filter(Boolean).join(' ').toLowerCase();
}

// attachment is optional: {attachmentUrl, attachmentType, thumbnailDataUrl}
// -- already-uploaded-to-R2 by the time this is called (the outbox only
// ever queues the small JSON POST, never re-attempts the upload itself;
// see DirectMessageThread's attachment send flow).
export async function queueOutboxMessage(friendId, content, attachment = null) {
  const db = await dbPromise;
  const localId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  await db.put('dm_outbox', {
    localId, friendId, content, createdAt: new Date().toISOString(),
    attachmentUrl: attachment?.attachmentUrl || null,
    attachmentType: attachment?.attachmentType || null,
    thumbnailDataUrl: attachment?.thumbnailDataUrl || null,
  });
  return localId;
}

export async function removeOutboxMessage(localId) {
  const db = await dbPromise;
  await db.delete('dm_outbox', localId);
}

export async function getOutboxForFriend(friendId) {
  const db = await dbPromise;
  const all = await db.getAll('dm_outbox');
  return all.filter((m) => m.friendId === friendId);
}

import { openDB } from 'idb';

// Read-through cache for local search: Postgres stays the source of truth.
// `conversations` holds list metadata + a lowercased searchText blob
// (title + transcript + chat, backfilled lazily as chats are opened).
const dbPromise = openDB('throughline', 1, {
  upgrade(db) {
    db.createObjectStore('conversations', { keyPath: 'id' });
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

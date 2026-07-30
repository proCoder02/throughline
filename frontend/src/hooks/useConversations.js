import { useCallback, useEffect, useState } from 'react';
import { apiJson, del } from '../api.js';
import { upsertConversationMeta, backfillConversationContent, removeConversation, searchConversations } from '../db.js';

export function useConversations() {
  const [list, setList] = useState([]);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState(null); // null = show `list` unfiltered

  const refresh = useCallback(async () => {
    const data = await apiJson('/conversations');
    setList(data);
    await Promise.all(data.map(upsertConversationMeta));
    return data;
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!query.trim()) { setResults(null); return; }
    let cancelled = false;
    searchConversations(query).then((r) => { if (!cancelled) setResults(r.map((x) => x.id)); });
    return () => { cancelled = true; };
  }, [query]);

  const openConversation = useCallback(async (id) => {
    const [conv, chat] = await Promise.all([
      apiJson(`/conversations/${id}`),
      apiJson(`/conversations/${id}/chat`),
    ]);
    await backfillConversationContent(id, conv.raw_transcript, chat);
    return { conv, chat };
  }, []);

  const removeConv = useCallback(async (id) => {
    await del(`/conversations/${id}`);
    await removeConversation(id);
    setList((l) => l.filter((c) => c.id !== id));
  }, []);

  const visible = results === null ? list : list.filter((c) => results.includes(c.id));

  return { list: visible, query, setQuery, refresh, openConversation, removeConv };
}

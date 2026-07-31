import { useEffect, useState } from 'react';
import ListPane from './ListPane.jsx';
import { BackIcon, TrashIcon, PencilIcon } from '../icons.jsx';
import { apiJson, post, del } from '../api.js';

export default function FriendsSection() {
  const [friends, setFriends] = useState([]);
  const [selected, setSelected] = useState(null);
  const [mood, setMood] = useState(null);
  const [code, setCode] = useState('');
  const [status, setStatus] = useState('');
  const [renaming, setRenaming] = useState(false);
  const [nicknameDraft, setNicknameDraft] = useState('');

  const load = () => apiJson('/friends').then(setFriends).catch(() => {});
  useEffect(() => { load(); }, []);

  // See ChatsSection's identical effect -- swaps list/detail on mobile widths.
  useEffect(() => {
    document.body.classList.toggle('has-active', !!selected);
    return () => document.body.classList.remove('has-active');
  }, [selected]);

  const addFriend = async () => {
    if (!code.trim()) return;
    setStatus('');
    try {
      const data = await post('/friends/add', { friend_code: code.trim() });
      setCode('');
      setStatus('Added ' + data.friend.username + '.');
      load();
    } catch (e) {
      setStatus(e.message);
    }
  };

  const openFriend = async (f) => {
    setSelected(f);
    const data = await apiJson(`/friends/${f.id}/mood`).catch(() => ({ entries: [] }));
    setMood(data.entries || []);
  };

  const removeFriend = async (f) => {
    if (!confirm(`Remove ${f.username} from your friends? This cannot be undone.`)) return;
    await del(`/friends/${f.id}`);
    if (selected?.id === f.id) setSelected(null);
    load();
  };

  const startRename = () => {
    setNicknameDraft(selected.nickname || '');
    setRenaming(true);
  };

  const saveNickname = async () => {
    const data = await post(`/friends/${selected.id}/nickname`, { nickname: nicknameDraft.trim() });
    setSelected((s) => ({ ...s, nickname: data.nickname }));
    setRenaming(false);
    load();
  };

  return (
    <>
      <ListPane title="Friends" emptyText="No friends added yet.">
        <div style={{ padding: '4px 16px 12px' }}>
          <div className="row2" style={{ display: 'flex', gap: 6 }}>
            <input className="field" style={{ marginTop: 0 }} placeholder="Friend's code" value={code} onChange={(e) => setCode(e.target.value)} />
            <button className="btn" onClick={addFriend}>Add</button>
          </div>
          {status && <div className="hint">{status}</div>}
        </div>
        {friends.map((f) => (
          <div key={f.id} className={'row' + (selected?.id === f.id ? ' active' : '')} onClick={() => openFriend(f)}>
            <span className="avatar">{(f.nickname || f.username)[0]}</span>
            <div className="row-main">
              <div className="row-top"><span className="row-title">{f.nickname || f.username}</span></div>
              <div className="row-sub"><span className="status-dot" />Mood tracked today</div>
            </div>
          </div>
        ))}
      </ListPane>
      <div className="detail-pane detail-view" style={{ flex: 1 }}>
        {selected ? (
          <div className="detail-card">
            <div className="detail-title-row">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                <button className="back-btn" title="Back" onClick={() => setSelected(null)}><BackIcon /></button>
                {renaming ? (
                  <input
                    className="field" style={{ marginTop: 0 }} autoFocus
                    placeholder={selected.username} value={nicknameDraft}
                    onChange={(e) => setNicknameDraft(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && saveNickname()}
                  />
                ) : (
                  <div className="detail-title">{(selected.nickname || selected.username)}'s mood today</div>
                )}
              </div>
              {renaming ? (
                <div style={{ display: 'flex', gap: 4 }}>
                  <button className="btn" onClick={saveNickname}>Save</button>
                  <button className="btn secondary" onClick={() => setRenaming(false)}>Cancel</button>
                </div>
              ) : (
                <div style={{ display: 'flex' }}>
                  <button className="conv-link-btn" title="Set nickname" onClick={startRename}>
                    <PencilIcon />
                  </button>
                  <button className="conv-link-btn" title="Remove friend" onClick={() => removeFriend(selected)}>
                    <TrashIcon />
                  </button>
                </div>
              )}
            </div>
            {mood && mood.length ? mood.map((m, i) => (
              <div key={i} className="detail-meta">
                {new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} — {m.mood_label}
              </div>
            )) : <div className="hint">No mood data logged yet today.</div>}
          </div>
        ) : (
          <div className="hint">Select a friend to see their mood through the day.</div>
        )}
      </div>
    </>
  );
}

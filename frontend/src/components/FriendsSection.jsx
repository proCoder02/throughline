import { useEffect, useState } from 'react';
import ListPane from './ListPane.jsx';
import { apiJson, post } from '../api.js';

export default function FriendsSection() {
  const [friends, setFriends] = useState([]);
  const [selected, setSelected] = useState(null);
  const [mood, setMood] = useState(null);
  const [code, setCode] = useState('');
  const [status, setStatus] = useState('');

  const load = () => apiJson('/friends').then(setFriends).catch(() => {});
  useEffect(() => { load(); }, []);

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
            <span className="avatar">{f.username[0]}</span>
            <div className="row-main">
              <div className="row-top"><span className="row-title">{f.username}</span></div>
              <div className="row-sub"><span className="status-dot" />Mood tracked today</div>
            </div>
          </div>
        ))}
      </ListPane>
      <div className="detail-pane" style={{ flex: 1 }}>
        {selected ? (
          <div className="detail-card">
            <div className="detail-title">{selected.username}'s mood today</div>
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

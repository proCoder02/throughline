import { useEffect, useState } from 'react';
import ListPane from './ListPane.jsx';
import { BackIcon, TrashIcon, PencilIcon, PhoneIcon, ChatIcon, CheckIcon, InfoIcon } from '../icons.jsx';
import { apiJson, post, del } from '../api.js';
import DirectMessageThread from './DirectMessageThread.jsx';

// WhatsApp/Telegram-style: today's time, "Yesterday", or a short date for
// anything older -- mirrors the Flutter client's formatCallTimestamp.
function formatCallTimestamp(iso) {
  const dt = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const that = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  const diffDays = Math.round((today - that) / 86400000);
  if (diffDays === 0) return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (diffDays === 1) return 'Yesterday';
  return dt.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// e.g. "14:32 (3)  Outgoing" -- the count only shows in parens when >1.
function formatLastCallSubtitle(f) {
  if (!f.last_call_at) return 'No calls yet';
  const countPart = f.call_count > 1 ? ` (${f.call_count})` : '';
  const direction = f.last_call_outgoing ? 'Outgoing' : 'Incoming';
  return `${formatCallTimestamp(f.last_call_at)}${countPart}  ${direction}`;
}

export default function FriendsSection({ onStartCall, notify, myUserId }) {
  const [friends, setFriends] = useState([]);
  const [selected, setSelected] = useState(null);
  // 'calls' (default, opened by clicking a friend row), 'mood' (opened via
  // the (i) button), or 'chat' (opened via the chat button) -- mirrors the
  // Flutter client's CallHistoryScreen/FriendMoodScreen/DirectMessageScreen.
  const [view, setView] = useState('calls');
  const [callHistory, setCallHistory] = useState(null);
  const [mood, setMood] = useState(null);
  const [code, setCode] = useState('');
  const [status, setStatus] = useState('');
  const [renaming, setRenaming] = useState(false);
  const [nicknameDraft, setNicknameDraft] = useState('');
  // Group-call picker: toggled from the header, turns each row into a
  // checkbox instead of opening their mood detail.
  const [pickingCall, setPickingCall] = useState(false);
  const [callSelection, setCallSelection] = useState([]);

  const load = () => apiJson('/friends').then(setFriends).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => {
    apiJson('/friends/unread_message_counts').then(notify.seedDmUnreadCounts).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    setView('calls');
    const data = await apiJson(`/friends/${f.id}/calls`).catch(() => []);
    setCallHistory(data);
  };

  const openChat = (e, f) => {
    e.stopPropagation();
    setSelected(f);
    setView('chat');
  };

  const showMood = async () => {
    setView('mood');
    const data = await apiJson(`/friends/${selected.id}/mood`).catch(() => null);
    setMood(data);
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

  const callFriend = (e, friendId) => {
    e.stopPropagation();
    onStartCall?.([friendId]).catch((err) => alert('Could not start call: ' + err.message));
  };

  const toggleCallSelection = (friendId) => {
    setCallSelection((prev) => (prev.includes(friendId) ? prev.filter((id) => id !== friendId) : [...prev, friendId]));
  };

  const confirmGroupCall = () => {
    if (!callSelection.length) return;
    const ids = callSelection;
    setPickingCall(false);
    setCallSelection([]);
    onStartCall?.(ids).catch((err) => alert('Could not start call: ' + err.message));
  };

  return (
    <>
      <ListPane
        title="Friends"
        headerAction={
          friends.length > 0 && (
            <button
              title={pickingCall ? 'Cancel group call' : 'Start a group call'}
              onClick={() => { setPickingCall((v) => !v); setCallSelection([]); }}
            >
              <PhoneIcon />
            </button>
          )
        }
        emptyText="No friends added yet."
      >
        <div style={{ padding: '4px 16px 12px' }}>
          <div className="row2" style={{ display: 'flex', gap: 6 }}>
            <input className="field" style={{ marginTop: 0 }} placeholder="Friend's code" value={code} onChange={(e) => setCode(e.target.value)} />
            <button className="btn" onClick={addFriend}>Add</button>
          </div>
          {status && <div className="hint">{status}</div>}
          {pickingCall && <div className="hint">Pick who to call, then confirm below.</div>}
        </div>
        {friends.map((f) => (
          <div
            key={f.id}
            className={'row' + (!pickingCall && selected?.id === f.id ? ' active' : '')}
            onClick={() => (pickingCall ? toggleCallSelection(f.id) : openFriend(f))}
          >
            <span className="avatar">{(f.nickname || f.username)[0]}</span>
            <div className="row-main">
              <div className="row-top"><span className="row-title">{f.nickname || f.username}</span></div>
              <div className="row-sub">{formatLastCallSubtitle(f)}</div>
            </div>
            {pickingCall ? (
              callSelection.includes(f.id) && <CheckIcon />
            ) : (
              <>
                <button className="conv-link-btn dm-chat-btn" title={`Message ${f.nickname || f.username}`} onClick={(e) => openChat(e, f)}>
                  <ChatIcon />
                  {notify.dmUnreadCounts[f.id] > 0 && <span className="dm-badge">{notify.dmUnreadCounts[f.id]}</span>}
                </button>
                <button className="conv-link-btn" title={`Call ${f.nickname || f.username}`} onClick={(e) => callFriend(e, f.id)}>
                  <PhoneIcon />
                </button>
              </>
            )}
          </div>
        ))}
        {pickingCall && (
          <div style={{ padding: '8px 16px' }}>
            <button className="btn" style={{ width: '100%' }} disabled={!callSelection.length} onClick={confirmGroupCall}>
              Call {callSelection.length || ''}
            </button>
          </div>
        )}
      </ListPane>
      {view === 'chat' && selected ? (
        <DirectMessageThread friend={selected} myUserId={myUserId} notify={notify} onBack={() => setSelected(null)} />
      ) : (
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
                  <div className="detail-title">
                    {(selected.nickname || selected.username)}{view === 'mood' ? "'s mood" : "'s calls"}
                  </div>
                )}
              </div>
              {renaming ? (
                <div style={{ display: 'flex', gap: 4 }}>
                  <button className="btn" onClick={saveNickname}>Save</button>
                  <button className="btn secondary" onClick={() => setRenaming(false)}>Cancel</button>
                </div>
              ) : (
                <div style={{ display: 'flex' }}>
                  {view === 'mood' ? (
                    <button className="conv-link-btn" title="View calls" onClick={() => setView('calls')}>
                      <PhoneIcon />
                    </button>
                  ) : (
                    <button className="conv-link-btn" title="View mood" onClick={showMood}>
                      <InfoIcon />
                    </button>
                  )}
                  <button className="conv-link-btn" title="Set nickname" onClick={startRename}>
                    <PencilIcon />
                  </button>
                  <button className="conv-link-btn" title="Remove friend" onClick={() => removeFriend(selected)}>
                    <TrashIcon />
                  </button>
                </div>
              )}
            </div>
            {view === 'mood' ? (
              mood?.emoji ? (
                <div className="mood-compiled">
                  <div className="mood-compiled-emoji">{mood.emoji}</div>
                  <div className="detail-meta" style={{ textTransform: 'capitalize' }}>{mood.mood_label}</div>
                  <div className="hint">
                    As of {new Date(mood.window_start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    {'–'}{new Date(mood.window_end).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </div>
                </div>
              ) : <div className="hint">No mood data logged yet today.</div>
            ) : callHistory?.length ? (
              callHistory.map((c) => (
                <div key={c.call_id} className="detail-meta-row">
                  <span>{c.outgoing ? 'Outgoing' : 'Incoming'}</span>
                  <span className="hint" style={{ margin: 0 }}>
                    {formatCallTimestamp(c.created_at)}
                    {c.ended_at && ` · ${Math.max(1, Math.round((new Date(c.ended_at) - new Date(c.created_at)) / 1000))}s`}
                  </span>
                </div>
              ))
            ) : <div className="hint">No calls yet.</div>}
          </div>
        ) : (
          <div className="hint">Select a friend to see their calls and mood.</div>
        )}
      </div>
      )}
    </>
  );
}

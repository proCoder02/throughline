import { useEffect, useState } from 'react';
import ListPane from './ListPane.jsx';
import { BackIcon, TrashIcon, PencilIcon, PhoneIcon, ChatIcon, CheckIcon } from '../icons.jsx';
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

const SHARING_LEVELS = [
  { value: 'off', label: 'Off', description: 'Your private cognitive information stays private.' },
  { value: 'limited', label: 'Limited', description: 'AI may use selected context to find mutually useful outcomes.' },
  { value: 'collaborative', label: 'Collaborative', description: 'AI can use approved context to actively help both of you coordinate.' },
];

export default function FriendsSection({ onStartCall, notify, myUserId }) {
  const [friends, setFriends] = useState([]);
  const [selected, setSelected] = useState(null);
  // 'profile' (default, opened by clicking a friend row -- shows everything
  // at once, WhatsApp-contact-screen style, no tab-switching) or 'chat'
  // (opened via the message button) -- mirrors the Flutter client's
  // DirectMessageScreen for the latter.
  const [view, setView] = useState('profile');
  const [callHistory, setCallHistory] = useState(null);
  const [mood, setMood] = useState(null);
  const [sharing, setSharing] = useState(null); // {my_level, both_enabled}
  const [savingSharing, setSavingSharing] = useState(false);
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

  // WhatsApp-contact-screen style: everything about this friend loads and
  // shows at once (mood, calls, cognitive sharing), no tab-switching.
  const openFriend = async (f) => {
    setSelected(f);
    setView('profile');
    setCallHistory(null);
    setMood(null);
    setSharing(null);
    apiJson(`/friends/${f.id}/calls`).then(setCallHistory).catch(() => setCallHistory([]));
    apiJson(`/friends/${f.id}/mood`).then(setMood).catch(() => setMood(null));
    apiJson(`/friends/${f.id}/cognitive-sharing`).then(setSharing).catch(() => setSharing(null));
  };

  const openChat = (e, f) => {
    e.stopPropagation();
    setSelected(f);
    setView('chat');
  };

  const setSharingLevel = async (level) => {
    if (!selected || savingSharing) return;
    setSavingSharing(true);
    try {
      const data = await post(`/friends/${selected.id}/cognitive-sharing`, { level });
      // Re-fetch rather than trust the POST response alone -- both_enabled
      // depends on the OTHER side's row too, which this response can't know.
      const fresh = await apiJson(`/friends/${selected.id}/cognitive-sharing`);
      setSharing(fresh);
    } catch (e) {
      setStatus(e.message);
    } finally {
      setSavingSharing(false);
    }
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
            <button className="back-btn" title="Back" onClick={() => setSelected(null)}><BackIcon /></button>

            {/* Header: big centered avatar + name, WhatsApp-contact-screen style */}
            <div style={{ textAlign: 'center', margin: '4px 0 16px' }}>
              <span className="avatar lg" style={{ fontSize: 24, margin: '0 auto 10px' }}>
                {(selected.nickname || selected.username)[0]}
              </span>
              {renaming ? (
                <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                  <input
                    className="field" style={{ marginTop: 0, maxWidth: 200 }} autoFocus
                    placeholder={selected.username} value={nicknameDraft}
                    onChange={(e) => setNicknameDraft(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && saveNickname()}
                  />
                  <button className="btn" onClick={saveNickname}>Save</button>
                  <button className="btn secondary" onClick={() => setRenaming(false)}>Cancel</button>
                </div>
              ) : (
                <>
                  <div className="detail-title" style={{ fontSize: 18 }}>{selected.nickname || selected.username}</div>
                  {selected.nickname && <div className="hint" style={{ margin: '0 0 6px' }}>{selected.username}</div>}
                  <button
                    className="conv-link-btn" title="Set nickname" onClick={startRename}
                    style={{ display: 'inline-flex', gap: 4, fontSize: 12, color: 'var(--wa-text-soft)' }}
                  >
                    <PencilIcon /> nickname
                  </button>
                </>
              )}
            </div>

            {/* Action row */}
            {!renaming && (
              <div style={{ display: 'flex', justifyContent: 'center', gap: 28, marginBottom: 20 }}>
                <button
                  className="conv-link-btn" title={`Message ${selected.nickname || selected.username}`}
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, fontSize: 11 }}
                  onClick={() => setView('chat')}
                >
                  <ChatIcon /> Message
                </button>
                <button
                  className="conv-link-btn" title={`Call ${selected.nickname || selected.username}`}
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, fontSize: 11 }}
                  onClick={(e) => callFriend(e, selected.id)}
                >
                  <PhoneIcon /> Call
                </button>
                <button
                  className="conv-link-btn" title="Remove friend"
                  style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--wa-danger)' }}
                  onClick={() => removeFriend(selected)}
                >
                  <TrashIcon /> Remove
                </button>
              </div>
            )}

            {/* Everything below is always visible -- no tab-switching */}

            <div className="profile-section">
              <div className="profile-section-title">Cognitive Sharing</div>
              {sharing ? (
                <>
                  <div className="hint" style={{ marginTop: 0 }}>
                    {sharing.both_enabled
                      ? `You and ${selected.nickname || selected.username} can both see mutually-helpful suggestions.`
                      : 'Both of you need to turn this on before suggestions can appear.'}
                  </div>
                  {SHARING_LEVELS.map((opt) => (
                    <label key={opt.value} className="sharing-option" style={{ opacity: savingSharing ? 0.6 : 1 }}>
                      <input
                        type="radio"
                        name="sharing-level"
                        checked={sharing.my_level === opt.value}
                        disabled={savingSharing}
                        onChange={() => setSharingLevel(opt.value)}
                      />
                      <div>
                        <div className="sharing-option-label">{opt.label}</div>
                        <div className="hint" style={{ margin: 0 }}>{opt.description}</div>
                      </div>
                    </label>
                  ))}
                </>
              ) : (
                <div className="hint" style={{ marginTop: 0 }}>Loading…</div>
              )}
            </div>

            <div className="profile-section">
              <div className="profile-section-title">Mood</div>
              {mood === null ? (
                <div className="hint" style={{ marginTop: 0 }}>Loading…</div>
              ) : mood?.emoji ? (
                <div className="mood-compiled">
                  <div className="mood-compiled-emoji">{mood.emoji}</div>
                  <div className="detail-meta" style={{ textTransform: 'capitalize' }}>{mood.mood_label}</div>
                  <div className="hint">
                    As of {new Date(mood.window_start).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    {'–'}{new Date(mood.window_end).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </div>
                </div>
              ) : <div className="hint" style={{ marginTop: 0 }}>No mood data logged yet today.</div>}
            </div>

            <div className="profile-section">
              <div className="profile-section-title">Calls</div>
              {callHistory === null ? (
                <div className="hint" style={{ marginTop: 0 }}>Loading…</div>
              ) : callHistory.length ? (
                callHistory.map((c) => (
                  <div key={c.call_id} className="detail-meta-row">
                    <span>{c.outgoing ? 'Outgoing' : 'Incoming'}</span>
                    <span className="hint" style={{ margin: 0 }}>
                      {formatCallTimestamp(c.created_at)}
                      {c.ended_at && ` · ${Math.max(1, Math.round((new Date(c.ended_at) - new Date(c.created_at)) / 1000))}s`}
                    </span>
                  </div>
                ))
              ) : <div className="hint" style={{ marginTop: 0 }}>No calls yet.</div>}
            </div>
          </div>
        ) : (
          <div className="hint">Select a friend to see their profile.</div>
        )}
      </div>
      )}
    </>
  );
}

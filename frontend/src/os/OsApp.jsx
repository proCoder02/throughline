import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJson, post, postForm, del } from '../api.js';
import { showToast, confirmDialog } from '../lib/notify.js';
import { uploadFile, getUploadsStatus } from '../lib/uploads.js';
import ChatsSection from '../components/ChatsSection.jsx';
import DirectMessageThread from '../components/DirectMessageThread.jsx';
import Avatar from '../components/Avatar.jsx';
import PhotoViewer from '../components/PhotoViewer.jsx';
import { CameraIcon, SunIcon, MoonIcon, TrashIcon, AttachIcon, CloseIcon, SendIcon } from '../icons.jsx';
import './os.css';

// The "Personal Intelligence OS" screen -- same IA/visual design as the
// approved mockup, but wired to the REAL backend end-to-end instead of
// static sample data. Mounted at /app/os as a sibling to the existing
// chats/tasks/profiles/friends/settings routes (see router.jsx), receiving
// the same auth/notify/call context App.jsx already hands every other
// section -- nothing here duplicates or bypasses existing auth, sockets, or
// call handling.
//
// The two hardest realtime features (live mic recording/transcription, and
// voice calls) are NOT reimplemented here -- Listen embeds the real,
// already-working ChatsSection component, and calls/DMs go through the same
// call.startCall/DirectMessageThread every other screen already uses. That
// was a deliberate choice: reimplementing WebSocket audio streaming or
// LiveKit signaling a second time would be pure risk with no upside over
// reusing code that already works in production.

const SHARING_LEVELS = [
  { value: 'off', label: 'Off', description: 'Your private cognitive information stays private.' },
  { value: 'limited', label: 'Limited', description: 'AI may use selected context to find mutually useful outcomes.' },
  { value: 'collaborative', label: 'Collaborative', description: 'AI can use approved context to actively help both of you coordinate.' },
];

const MOOD_HEIGHT = { happy: 85, excited: 92, positive: 80, calm: 62, neutral: 50, tired: 35, sad: 25, angry: 30, anxious: 40, stressed: 32 };
const moodHeight = (label) => MOOD_HEIGHT[(label || '').toLowerCase()] ?? 50;
const isGoodMood = (label) => ['happy', 'excited', 'positive', 'calm'].includes((label || '').toLowerCase());
const isNeutralMood = (label) => ['neutral'].includes((label || '').toLowerCase());

function initials(name) {
  return (name || '?').trim().charAt(0).toUpperCase() || '?';
}

function timeGreeting() {
  const h = new Date().getHours();
  if (h < 12) return 'morning';
  if (h < 18) return 'afternoon';
  return 'evening';
}

function relativeDay(iso) {
  if (!iso) return '';
  const dt = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const that = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  const diffDays = Math.round((today - that) / 86400000);
  if (diffDays === 0) return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (diffDays === 1) return 'Yesterday';
  return dt.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function Toggle({ on, onChange, disabled }) {
  return (
    <button
      type="button"
      className={'toggle' + (on ? ' on' : '')}
      disabled={disabled}
      style={disabled ? { opacity: 0.5, cursor: 'default' } : undefined}
      onClick={() => !disabled && onChange(!on)}
    >
      <span />
    </button>
  );
}

function DecorativeToggle({ defaultOn = false }) {
  const [on, setOn] = useState(defaultOn);
  return <Toggle on={on} onChange={setOn} />;
}

function NavItem({ icon, label, screenKey, navActive, go }) {
  return (
    <button
      className={'nav-item' + (navActive === screenKey ? ' active' : '')}
      onClick={() => go(screenKey, screenKey)}
    >
      <span className="nav-icon">{icon}</span>
      <span>{label}</span>
    </button>
  );
}

export default function OsApp({ auth, notify, call, theme, toggleTheme }) {
  const navigate = useNavigate();
  const user = auth?.user;

  const [screen, setScreen] = useState('home');
  const [navActive, setNavActive] = useState('home');
  function go(screenKey, navKey = null) {
    setScreen(screenKey);
    setNavActive(navKey);
  }
  const screenClass = (key) => 'screen' + (screen === key ? ' active' : '');

  // ---- Core lists, loaded once up front (small, personal-scale data) ----
  const [conversations, setConversations] = useState(null); // null = loading
  const [tasks, setTasks] = useState(null);
  const [profiles, setProfiles] = useState({});
  const [friends, setFriends] = useState([]);
  const [moodDays, setMoodDays] = useState([]);
  const [moodStreak, setMoodStreak] = useState(0);
  const [digest, setDigest] = useState(null);
  const [settings, setSettings] = useState(null);
  const [nudgeSettings, setNudgeSettings] = useState(null);

  useEffect(() => {
    apiJson('/conversations').then(setConversations).catch(() => setConversations([]));
    apiJson('/tasks?status=all').then(setTasks).catch(() => setTasks([]));
    apiJson('/profiles').then(setProfiles).catch(() => setProfiles({}));
    apiJson('/friends').then(setFriends).catch(() => setFriends([]));
    apiJson('/mood/history?days=56').then((d) => { setMoodDays(d.days || []); setMoodStreak(d.streak || 0); }).catch(() => {});
    apiJson('/insights/digest').then((d) => setDigest(d.digest || null)).catch(() => setDigest(null));
    apiJson('/settings').then(setSettings).catch(() => {});
    apiJson('/settings/nudges').then(setNudgeSettings).catch(() => {});
  }, []);

  useEffect(() => {
    apiJson('/friends/unread_message_counts').then(notify.seedDmUnreadCounts).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openTasksCount = useMemo(() => (tasks || []).filter((t) => t.status === 'open').length, [tasks]);
  const doneTasksCount = useMemo(() => (tasks || []).filter((t) => t.status === 'done').length, [tasks]);
  const dueTodayCount = useMemo(() => {
    const today = new Date().toDateString();
    return (tasks || []).filter((t) => t.status === 'open' && t.due_date && new Date(t.due_date).toDateString() === today).length;
  }, [tasks]);
  const peopleList = useMemo(() => {
    const friendItems = friends.map((f) => ({ kind: 'friend', key: 'f' + f.id, name: f.nickname || f.username, friend: f }));
    const profileItems = Object.entries(profiles).map(([name, p]) => ({
      kind: 'profile', key: 'p' + p.profile_id, name, profileId: p.profile_id,
      categories: p.categories || [], lastSeen: p.last_seen, noteCount: (p.notes || []).length,
    }));
    return [...friendItems, ...profileItems];
  }, [profiles, friends]);
  const peopleCount = peopleList.length;

  // ---- Conversations screen: real list + real per-conversation Q&A ----
  const [selectedConvId, setSelectedConvId] = useState(null);
  const [convMessages, setConvMessages] = useState(null);
  const [convInput, setConvInput] = useState('');
  const [convSending, setConvSending] = useState(false);
  const selectedConv = (conversations || []).find((c) => c.id === selectedConvId) || null;

  function openConversation(id) {
    setSelectedConvId(id);
    setConvMessages(null);
    apiJson(`/conversations/${id}/chat`).then(setConvMessages).catch(() => setConvMessages([]));
  }
  useEffect(() => {
    if (conversations && conversations.length && selectedConvId === null) openConversation(conversations[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversations]);

  async function sendConversationMessage() {
    const text = convInput.trim();
    if (!text || !selectedConvId || convSending) return;
    setConvInput('');
    setConvSending(true);
    setConvMessages((m) => [...(m || []), { role: 'user', content: text, created_at: new Date().toISOString() }]);
    try {
      const data = await post('/chat', { prompt: text, conversation_id: selectedConvId });
      setConvMessages((m) => [...(m || []), { role: 'assistant', content: data.reply || '(no reply)', created_at: new Date().toISOString() }]);
    } catch (e) {
      showToast('Failed to send: ' + e.message, 'error');
    } finally {
      setConvSending(false);
    }
  }

  // ---- Tasks screen: real complete/reopen ----
  async function toggleTask(task) {
    const wasDone = task.status === 'done';
    setTasks((ts) => ts.map((t) => (t.id === task.id ? { ...t, status: wasDone ? 'open' : 'done' } : t)));
    try {
      await post(`/tasks/${task.id}/${wasDone ? 'reopen' : 'complete'}`, {});
    } catch (e) {
      showToast('Failed to update task: ' + e.message, 'error');
      setTasks((ts) => ts.map((t) => (t.id === task.id ? { ...t, status: task.status } : t)));
    }
  }

  // ---- People screen: real DM thread + real calls + real add-by-code ----
  const [dmFriend, setDmFriend] = useState(null);
  const [addingFriend, setAddingFriend] = useState(false);
  const [friendCode, setFriendCode] = useState('');
  const [friendAddStatus, setFriendAddStatus] = useState('');
  function messageFriend(friend) { setDmFriend(friend); }
  function callFriend(friendId) {
    call.startCall([friendId]).catch((err) => showToast('Could not start call: ' + err.message, 'error'));
  }
  async function addFriend() {
    if (!friendCode.trim()) return;
    setFriendAddStatus('');
    try {
      const data = await post('/friends/add', { friend_code: friendCode.trim() });
      setFriendCode('');
      setAddingFriend(false);
      setFriendAddStatus('Added ' + data.friend.username + '.');
      apiJson('/friends').then(setFriends).catch(() => {});
    } catch (e) {
      setFriendAddStatus(e.message);
    }
  }

  // ---- Calls screen: aggregated real per-friend call history ----
  const [callLog, setCallLog] = useState(null);
  const [pickingCall, setPickingCall] = useState(false);
  const [callSelection, setCallSelection] = useState([]);
  useEffect(() => {
    if (screen !== 'calls' || callLog !== null) return;
    if (!friends.length) { setCallLog([]); return; }
    Promise.all(
      friends.map((f) =>
        apiJson(`/friends/${f.id}/calls`)
          .then((calls) => (calls || []).map((c) => ({ ...c, friend: f })))
          .catch(() => [])
      )
    ).then((lists) => setCallLog(lists.flat().sort((a, b) => new Date(b.created_at) - new Date(a.created_at))));
  }, [screen, friends, callLog]);
  function toggleCallSelection(id) {
    setCallSelection((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }
  function confirmGroupCall() {
    if (!callSelection.length) return;
    const ids = callSelection;
    setPickingCall(false);
    setCallSelection([]);
    call.startCall(ids).catch((err) => showToast('Could not start call: ' + err.message, 'error'));
  }

  // ---- Ask screen: real cross-session assistant, as an actual scrolling
  // thread (ChatGPT-style: history above, composer pinned at the bottom)
  // rather than a single search-box-with-one-reply-below layout -- loads
  // real prior history from GET /chat/global (same persisted thread the
  // real ChatsSection's own global chat already reads/writes), and every
  // new turn appends to it instead of replacing a single "reply" slot.
  // Image attach uses the same /chat/global/image endpoint the existing
  // ChatsSection's own global chat already uses -- a direct multipart
  // upload, not the presigned-R2 flow DMs use, so it works regardless of
  // R2_STORAGE_ENABLED. ----
  const [askHistory, setAskHistory] = useState(null); // null = loading
  const [askInput, setAskInput] = useState('');
  const [askLoading, setAskLoading] = useState(false);
  const [askImageFile, setAskImageFile] = useState(null);
  const [askImagePreview, setAskImagePreview] = useState(null);
  const askImageInputRef = useRef(null);
  const askMessagesRef = useRef(null);

  useEffect(() => {
    apiJson('/chat/global').then(setAskHistory).catch(() => setAskHistory([]));
  }, []);
  useEffect(() => {
    const el = askMessagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [askHistory, askLoading]);

  function fillAsk(text) { setAskInput(text); }
  function pickAskImage() { askImageInputRef.current?.click(); }
  function onAskImageSelected(e) {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { showToast('Image is too large (max 10MB)', 'error'); return; }
    setAskImageFile(file);
    setAskImagePreview(URL.createObjectURL(file));
  }
  function removeAskImage() {
    if (askImagePreview) URL.revokeObjectURL(askImagePreview);
    setAskImageFile(null);
    setAskImagePreview(null);
  }
  async function askThroughline() {
    const text = askInput.trim();
    if (!text || askLoading) return;
    setAskInput('');
    setAskLoading(true);
    try {
      if (askImageFile) {
        const imagePreview = askImagePreview;
        const form = new FormData();
        form.append('image', askImageFile);
        form.append('description', text);
        setAskImageFile(null);
        setAskImagePreview(null);
        const data = await postForm('/chat/global/image', form);
        setAskHistory((h) => [...(h || []), { role: 'user', content: text, imageUrl: imagePreview }, { role: 'assistant', content: data.reply || '(no reply)' }]);
      } else {
        setAskHistory((h) => [...(h || []), { role: 'user', content: text }]);
        const data = await post('/chat/global', { prompt: text });
        setAskHistory((h) => [...(h || []), { role: 'assistant', content: data.reply || '(no reply)' }]);
      }
    } catch (e) {
      setAskHistory((h) => [...(h || []), { role: 'assistant', content: 'Something went wrong: ' + e.message }]);
    } finally {
      setAskLoading(false);
    }
  }

  // ---- Sharing screen: real per-friend cognitive-sharing level ----
  const [sharingMap, setSharingMap] = useState({});
  const [sharingSavingId, setSharingSavingId] = useState(null);
  useEffect(() => {
    if (screen !== 'sharing') return;
    friends.forEach((f) => {
      if (sharingMap[f.id] !== undefined) return;
      apiJson(`/friends/${f.id}/cognitive-sharing`).then((d) => setSharingMap((m) => ({ ...m, [f.id]: d }))).catch(() => {});
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [screen, friends]);
  async function setFriendSharing(friendId, level) {
    setSharingSavingId(friendId);
    try {
      await post(`/friends/${friendId}/cognitive-sharing`, { level });
      const fresh = await apiJson(`/friends/${friendId}/cognitive-sharing`);
      setSharingMap((m) => ({ ...m, [friendId]: fresh }));
    } catch (e) {
      showToast('Failed to update sharing: ' + e.message, 'error');
    } finally {
      setSharingSavingId(null);
    }
  }

  // ---- Commerce + Settings screens: real Swiggy connection status. Shape
  // is {enabled, accounts: {food|im|dineout: {connected, ...}}} -- confirmed
  // against SettingsSection.jsx's own real usage of this same endpoint. ----
  const SWIGGY_SERVER_LABELS = { food: 'Swiggy Food', im: 'Swiggy Instamart', dineout: 'Swiggy Dineout' };
  const [swiggy, setSwiggy] = useState(null);
  const loadSwiggy = () => apiJson('/integrations/swiggy/status').then(setSwiggy).catch(() => setSwiggy({ enabled: false }));
  useEffect(() => {
    if ((screen !== 'commerce' && screen !== 'settings') || swiggy !== null) return;
    loadSwiggy();
  }, [screen, swiggy]);
  async function disconnectSwiggy(server) {
    try { await post('/integrations/swiggy/disconnect', { server }); loadSwiggy(); } catch (e) { showToast(e.message, 'error'); }
  }

  // ---- Settings screen: real personalization + real smart-feature flags +
  // real profile picture, categories, and logout (full parity with the old
  // SettingsSection, so this screen is no longer just a link-out). ----
  const [categories, setCategories] = useState({ builtin: [], custom: [] });
  const [newCategory, setNewCategory] = useState('');
  const [categoryError, setCategoryError] = useState('');
  const [uploadsEnabled, setUploadsEnabled] = useState(false);
  const [uploadingPicture, setUploadingPicture] = useState(false);
  const [viewingOwnPhoto, setViewingOwnPhoto] = useState(false);
  const pictureInputRef = useRef(null);

  useEffect(() => {
    apiJson('/categories').then(setCategories).catch(() => {});
    getUploadsStatus().then((s) => setUploadsEnabled(!!s.enabled));
  }, []);

  async function updatePersonalization(value) {
    setSettings((s) => ({ ...s, personalization: value }));
    try { await post('/settings', { personalization: value }); } catch (e) { showToast(e.message, 'error'); }
  }
  async function updateNudge(field, value) {
    if (field !== 'smart_features_enabled' && !nudgeSettings?.smart_features_enabled) {
      showToast('Turn on Smart features before changing an individual feature', 'error');
      return;
    }
    const prev = nudgeSettings;
    setNudgeSettings((s) => ({ ...s, [field]: value }));
    try {
      const result = await post('/settings/nudges', { [field]: value });
      setNudgeSettings(result);
    } catch (e) {
      showToast(e.message, 'error');
      setNudgeSettings(prev);
    }
  }
  function pickProfilePicture() { pictureInputRef.current?.click(); }
  async function onProfilePictureSelected(e) {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    setUploadingPicture(true);
    try {
      const objectKey = await uploadFile(file, 'profile_picture');
      const { profile_picture_url } = await post('/profile/picture', { object_key: objectKey });
      auth.updateUser?.({ profile_picture_url });
      setSettings((s) => ({ ...s, profile_picture_url }));
      showToast('Profile picture updated', 'success');
    } catch (err) {
      showToast('Could not update profile picture: ' + err.message, 'error');
    } finally {
      setUploadingPicture(false);
    }
  }
  async function addCategory() {
    const name = newCategory.trim();
    if (!name) return;
    setCategoryError('');
    try {
      await post('/categories', { name });
      setNewCategory('');
      apiJson('/categories').then(setCategories);
    } catch (e) {
      setCategoryError(e.message);
    }
  }
  async function removeCategory(name) {
    const ok = await confirmDialog({ title: `Delete "${name}"?`, message: 'Conversations already tagged with it keep the label.', confirmLabel: 'Delete', danger: true });
    if (!ok) return;
    await del(`/categories/${encodeURIComponent(name)}`);
    apiJson('/categories').then(setCategories);
  }

  // ---- Notifications screen: derived from real state, nothing invented ----
  const derivedNotifications = useMemo(() => {
    const items = [];
    const now = Date.now();
    (tasks || []).forEach((t) => {
      if (t.status !== 'open' || !t.due_date) return;
      const due = new Date(t.due_date).getTime();
      if (due - now < 2 * 86400000) {
        items.push({
          id: 'task-' + t.id, avatar: '✦', badge: due < now ? 'orange' : '',
          badgeLabel: due < now ? 'Overdue' : 'Task', title: due < now ? 'Task overdue' : 'Task due soon', meta: t.description,
        });
      }
    });
    Object.entries(notify.dmUnreadCounts || {}).forEach(([fid, count]) => {
      if (!count) return;
      const f = friends.find((x) => String(x.id) === String(fid));
      items.push({
        id: 'dm-' + fid, avatar: initials(f ? (f.nickname || f.username) : '?'), badge: 'blue', badgeLabel: 'Message',
        title: `${f ? (f.nickname || f.username) : 'A friend'} sent you a message`, meta: `${count} unread`,
      });
    });
    if (digest && digest.length) {
      items.push({ id: 'digest', avatar: '✦', badge: '', badgeLabel: 'Insight', title: 'Weekly digest ready', meta: `${digest.length} new cognitive insight${digest.length === 1 ? '' : 's'}.` });
    }
    return items;
  }, [tasks, notify.dmUnreadCounts, friends, digest]);

  // ---- Mood chart + heatmap: real per-day mood, reconstructed as a full
  // calendar grid so days with no log show up honestly as blank, not skipped. ----
  const moodByDate = useMemo(() => {
    const map = new Map();
    moodDays.forEach((d) => map.set(d.date, d));
    return map;
  }, [moodDays]);
  const heatmapCells = useMemo(() => {
    const cells = [];
    const today = new Date();
    for (let i = 55; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      cells.push(moodByDate.get(d.toISOString().slice(0, 10)) || null);
    }
    return cells;
  }, [moodByDate]);
  const trendCells = useMemo(() => {
    const cells = [];
    const today = new Date();
    for (let i = 6; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      cells.push(moodByDate.get(d.toISOString().slice(0, 10)) || null);
    }
    return cells;
  }, [moodByDate]);

  const recentConversations = (conversations || []).slice(0, 3);
  const digestHeadline = digest && digest.length ? (digest[0].text || digest[0].title || digest[0].summary || digest[0].description) : null;

  // Screens whose content should fill the remaining viewport height exactly
  // (own internal scrollbars, like a real chat app) instead of growing the
  // outer page -- everything else scrolls normally within .content.
  const isFillScreen = screen === 'conversations' || screen === 'listen' || screen === 'ask' || (screen === 'people' && !!dmFriend);

  return (
    <div className="tl-os">
      <div className="app">
        {/* ========================= SIDEBAR ========================= */}
        <aside className="sidebar">
          <div className="logo">
            <div className="logo-mark">T</div>
            <div className="logo-text">Throughline</div>
          </div>

          <div className="nav-section">
            <div className="nav-title">Workspace</div>
            <NavItem icon="✦" label="Home" screenKey="home" navActive={navActive} go={go} />
            <NavItem icon="◉" label="Conversations" screenKey="conversations" navActive={navActive} go={go} />
            <NavItem icon="◎" label="People" screenKey="people" navActive={navActive} go={go} />
            <NavItem icon="□" label="Tasks" screenKey="tasks" navActive={navActive} go={go} />
            <NavItem icon="☎" label="Calls" screenKey="calls" navActive={navActive} go={go} />
          </div>

          <div className="nav-section">
            <div className="nav-title">Intelligence</div>
            <NavItem icon="✧" label="Insights" screenKey="insights" navActive={navActive} go={go} />
            <NavItem icon="⌕" label="Ask Throughline" screenKey="ask" navActive={navActive} go={go} />
            <NavItem icon="◇" label="Cognitive Sharing" screenKey="sharing" navActive={navActive} go={go} />
            <NavItem icon="⌁" label="Commerce" screenKey="commerce" navActive={navActive} go={go} />
          </div>

          <div className="nav-section">
            <div className="nav-title">System</div>
            <NavItem icon="♢" label="Notifications" screenKey="notifications" navActive={navActive} go={go} />
            <NavItem icon="⚙" label="Settings" screenKey="settings" navActive={navActive} go={go} />
          </div>

          <div className="sidebar-bottom">
            <div className="profile-mini">
              <Avatar url={user?.profile_picture_url} name={user?.username} />
              <div className="profile-info">
                <div className="profile-name">{user?.username}</div>
                <div className="profile-status">● {notify.connected ? 'Online' : 'Reconnecting...'}</div>
              </div>
            </div>
          </div>
        </aside>

        {/* ========================= MAIN ========================= */}
        <main className="main">
          <header className="topbar">
            <div className="global-search">
              ⌕
              <input placeholder="Search conversations, people, memories..." onClick={() => go('ask')} readOnly />
            </div>

            <div className="top-actions">
              <button className="icon-button" onClick={() => go('notifications')}>
                ♢{derivedNotifications.length > 0 && ' ' + derivedNotifications.length}
              </button>
              <select
                className="context-select"
                value={settings?.personalization || 'personal'}
                onChange={(e) => updatePersonalization(e.target.value)}
              >
                <option value="personal">Personal</option>
                <option value="office">Office</option>
                <option value="study">Study</option>
              </select>
              <Avatar url={user?.profile_picture_url} name={user?.username} />
            </div>
          </header>

          <div className={'content' + (isFillScreen ? ' content-fill' : '')}>
            {/* ========================= HOME ========================= */}
            <section className={screenClass('home')}>
              <div className="hero">
                <div className="ai-pill">
                  <span className="pulse" />
                  Cognitive system active
                </div>
                <h1 style={{ marginTop: 15 }}>Good {timeGreeting()}, {user?.username}.</h1>
                <p>Here's what Throughline thinks matters right now.</p>
                <button className="primary-button" onClick={() => go('ask')}>Ask Throughline</button>
              </div>

              <div className="grid-4">
                <div className="card">
                  <div className="card-title">Conversations</div>
                  <div className="card-value">{conversations === null ? '…' : conversations.length}</div>
                  <div className="card-sub">Your full history</div>
                </div>
                <div className="card">
                  <div className="card-title">Open tasks</div>
                  <div className="card-value">{tasks === null ? '…' : openTasksCount}</div>
                  <div className="card-sub">{dueTodayCount} due today</div>
                </div>
                <div className="card">
                  <div className="card-title">People</div>
                  <div className="card-value">{peopleCount}</div>
                  <div className="card-sub">{friends.length} friends</div>
                </div>
                <div className="card">
                  <div className="card-title">Mood streak</div>
                  <div className="card-value">{moodStreak}d</div>
                  <div className="card-sub">Consecutive days logged</div>
                </div>
              </div>

              <div className="grid-2" style={{ marginTop: 20 }}>
                <div className="card insight-card">
                  <div className="insight-icon">✦</div>
                  <div className="card-title">Throughline noticed</div>
                  <h2>{digestHeadline || 'Keep talking -- your first weekly insight appears once there\'s enough to look at.'}</h2>
                  <button className="secondary-button" style={{ marginTop: 18 }} onClick={() => go('insights')}>
                    Explore connection →
                  </button>
                </div>

                <div className="card">
                  <div className="card-title">Most recent</div>
                  {recentConversations[0] ? (
                    <>
                      <h3>{recentConversations[0].title || 'Untitled conversation'}</h3>
                      <p className="card-sub">{relativeDay(recentConversations[0].created_at)} · {recentConversations[0].category || 'personal'}</p>
                      <div style={{ marginTop: 18 }}>
                        <button className="primary-button" onClick={() => { openConversation(recentConversations[0].id); go('conversations'); }}>
                          Open conversation
                        </button>
                      </div>
                    </>
                  ) : <p className="card-sub">No conversations recorded yet.</p>}
                </div>
              </div>

              <div className="card" style={{ marginTop: 20 }}>
                <div className="page-heading" style={{ marginBottom: 5 }}>
                  <div><h2 style={{ fontSize: 18 }}>Recent conversations</h2></div>
                  <button className="secondary-button" onClick={() => go('conversations')}>View all</button>
                </div>

                <div className="list">
                  {conversations === null ? (
                    <div className="list-item"><div className="item-main"><div className="item-meta">Loading…</div></div></div>
                  ) : recentConversations.length ? recentConversations.map((c) => (
                    <div key={c.id} className="list-item" style={{ cursor: 'pointer' }} onClick={() => { openConversation(c.id); go('conversations'); }}>
                      <div className="avatar">{initials(c.title)}</div>
                      <div className="item-main">
                        <div className="item-title">{c.title || 'Untitled conversation'}</div>
                        <div className="item-meta">{relativeDay(c.created_at)}</div>
                      </div>
                      <span className="badge">{c.category || 'personal'}</span>
                    </div>
                  )) : <div className="list-item"><div className="item-main"><div className="item-meta">Nothing recorded yet -- try Listen.</div></div></div>}
                </div>
              </div>
            </section>

            {/* ========================= CONVERSATIONS ========================= */}
            <section className={screenClass('conversations')}>
              <div className="page-heading">
                <div>
                  <h1>Conversations</h1>
                  <p>Your complete conversational memory.</p>
                </div>
                <button className="primary-button" onClick={() => go('listen')}>🎙 Listen</button>
              </div>

              <div className="chat-layout">
                <div className="chat-list">
                  <input className="chat-search" placeholder="Search conversations..." disabled />
                  {conversations === null ? (
                    <div className="item-meta" style={{ padding: 12 }}>Loading…</div>
                  ) : conversations.length ? conversations.map((c) => (
                    <div
                      key={c.id}
                      className={'conversation' + (selectedConvId === c.id ? ' selected' : '')}
                      onClick={() => openConversation(c.id)}
                    >
                      <div className="avatar">{initials(c.title)}</div>
                      <div className="item-main">
                        <div className="item-title">{c.title || 'Untitled conversation'}</div>
                        <div className="item-meta">{relativeDay(c.created_at)}</div>
                      </div>
                    </div>
                  )) : <div className="item-meta" style={{ padding: 12 }}>No conversations yet.</div>}
                </div>

                <div className="chat-window">
                  {selectedConv ? (
                    <>
                      <div className="chat-header">
                        <div className="avatar">{initials(selectedConv.title)}</div>
                        <div>
                          <div className="item-title">{selectedConv.title || 'Untitled conversation'}</div>
                          <div className="item-meta">{selectedConv.category || 'personal'} · {relativeDay(selectedConv.created_at)}</div>
                        </div>
                      </div>

                      <div className="messages">
                        {convMessages === null ? (
                          <div className="item-meta">Loading…</div>
                        ) : convMessages.length ? convMessages.map((m, i) => (
                          <div key={i} className={'message' + (m.role === 'user' ? ' me' : '')}>{m.content}</div>
                        )) : <div className="item-meta">Ask something about this conversation below.</div>}
                      </div>

                      <div className="chat-input">
                        <input
                          placeholder="Ask about this conversation..."
                          value={convInput}
                          onChange={(e) => setConvInput(e.target.value)}
                          onKeyDown={(e) => e.key === 'Enter' && sendConversationMessage()}
                        />
                        <button className="primary-button" disabled={convSending} onClick={sendConversationMessage}>
                          {convSending ? '…' : 'Send'}
                        </button>
                      </div>
                    </>
                  ) : (
                    <div className="messages"><div className="item-meta">Select a conversation.</div></div>
                  )}
                </div>
              </div>
            </section>

            {/* ========================= LISTEN ========================= */}
            {/* Real, already-working recording/live-transcription flow --
                embedded rather than reimplemented (see file header note). */}
            <section className={screenClass('listen')}>
              <div className="page-heading">
                <div>
                  <h1>Listen</h1>
                  <p>Turn conversations into searchable intelligence.</p>
                </div>
              </div>
              <div className="fill-panel">
                <ChatsSection notify={notify} openConversationId={null} onConsumeOpenConversationId={() => {}} />
              </div>
            </section>

            {/* ========================= TASKS ========================= */}
            <section className={screenClass('tasks')}>
              <div className="page-heading">
                <div>
                  <h1>Tasks</h1>
                  <p>Things Throughline noticed you need to do.</p>
                </div>
              </div>

              <div className="grid-4">
                <div className="card"><div className="card-title">Open</div><div className="card-value">{openTasksCount}</div></div>
                <div className="card"><div className="card-title">Due today</div><div className="card-value">{dueTodayCount}</div></div>
                <div className="card"><div className="card-title">Completed</div><div className="card-value">{doneTasksCount}</div></div>
                <div className="card"><div className="card-title">AI extracted</div><div className="card-value">{(tasks || []).length}</div></div>
              </div>

              <div className="card" style={{ marginTop: 20 }}>
                <div className="list">
                  {tasks === null ? (
                    <div className="item-meta" style={{ padding: 12 }}>Loading…</div>
                  ) : tasks.length ? tasks.map((t) => (
                    <div key={t.id} className="list-item">
                      <input type="checkbox" checked={t.status === 'done'} onChange={() => toggleTask(t)} />
                      <div className="item-main">
                        <div className="item-title" style={t.status === 'done' ? { textDecoration: 'line-through', color: '#9ca3af' } : undefined}>
                          {t.description}
                        </div>
                        <div className="item-meta">
                          {t.status === 'done' ? 'Completed' : t.due_date ? `Due ${new Date(t.due_date).toLocaleDateString()}` : 'No due date'}
                        </div>
                      </div>
                      <span className={'badge' + (t.status === 'done' ? ' green' : t.category === 'study' ? ' blue' : t.category === 'office' ? ' orange' : '')}>
                        {t.status === 'done' ? 'Done' : (t.category || 'personal')}
                      </span>
                    </div>
                  )) : <div className="item-meta" style={{ padding: 12 }}>No tasks yet -- they're extracted automatically from your conversations.</div>}
                </div>
              </div>
            </section>

            {/* ========================= PEOPLE ========================= */}
            <section className={screenClass('people')}>
              <div className="page-heading">
                <div>
                  <h1>People</h1>
                  <p>People Throughline remembers from your conversations.</p>
                </div>
                <button className="secondary-button" onClick={() => setAddingFriend((v) => !v)}>{addingFriend ? 'Cancel' : '+ Add friend'}</button>
              </div>

              {addingFriend && (
                <div className="card" style={{ marginBottom: 18 }}>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <input
                      className="chat-search" style={{ margin: 0 }} placeholder="Friend's code"
                      value={friendCode} onChange={(e) => setFriendCode(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && addFriend()}
                    />
                    <button className="primary-button" onClick={addFriend}>Add</button>
                  </div>
                  {friendAddStatus && <p className="card-sub">{friendAddStatus}</p>}
                </div>
              )}

              {dmFriend ? (
                <div className="fill-panel">
                  <DirectMessageThread
                    friend={dmFriend}
                    myUserId={user?.id}
                    notify={notify}
                    onBack={() => setDmFriend(null)}
                    onViewProfile={() => setDmFriend(null)}
                  />
                </div>
              ) : (
                <div className="grid-3">
                  {peopleList.length === 0 && <div className="card-sub">Nobody yet -- add a friend or record a conversation.</div>}
                  {peopleList.map((p) => (
                    <div className="card person-card" key={p.key}>
                      <div className="person-top">
                        <div className="person-avatar">{initials(p.name)}</div>
                        <div>
                          <div className="person-name">{p.name}</div>
                          <div className="person-role">
                            {p.kind === 'friend' ? 'Friend' : 'Mentioned in your recordings'}
                          </div>
                        </div>
                      </div>
                      <span className={'badge' + (p.kind === 'friend' ? ' green' : '')}>
                        {p.kind === 'friend' ? 'Friend' : `${p.noteCount} note${p.noteCount === 1 ? '' : 's'}`}
                      </span>
                      <div className="chips">
                        {(p.kind === 'friend' ? [] : p.categories).map((c) => <span className="chip" key={c}>{c}</span>)}
                      </div>
                      {p.kind === 'friend' ? (
                        <div style={{ display: 'flex', gap: 8, marginTop: 13 }}>
                          <button className="secondary-button" onClick={() => messageFriend(p.friend)}>Message</button>
                          <button className="secondary-button" onClick={() => callFriend(p.friend.id)}>Call</button>
                        </div>
                      ) : (
                        <button className="secondary-button" style={{ marginTop: 13 }} onClick={() => navigate('/app/profiles')}>
                          View profile →
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* ========================= CALLS ========================= */}
            <section className={screenClass('calls')}>
              <div className="page-heading">
                <div>
                  <h1>Calls</h1>
                  <p>Voice conversations connected to your cognitive history.</p>
                </div>
                <button className="primary-button" onClick={() => { setPickingCall((v) => !v); setCallSelection([]); }}>
                  ☎ {pickingCall ? 'Cancel' : 'Start call'}
                </button>
              </div>

              {pickingCall && (
                <div className="card" style={{ marginBottom: 18 }}>
                  <div className="card-title">Pick who to call</div>
                  <div className="chips">
                    {friends.map((f) => (
                      <span
                        key={f.id}
                        className="chip"
                        style={{ cursor: 'pointer', background: callSelection.includes(f.id) ? 'var(--accent-light)' : undefined, color: callSelection.includes(f.id) ? '#0f766e' : undefined }}
                        onClick={() => toggleCallSelection(f.id)}
                      >
                        {f.nickname || f.username}
                      </span>
                    ))}
                  </div>
                  <button className="primary-button" style={{ marginTop: 14 }} disabled={!callSelection.length} onClick={confirmGroupCall}>
                    Call {callSelection.length || ''}
                  </button>
                </div>
              )}

              <div className="grid-3">
                {callLog === null ? (
                  <div className="card-sub">Loading…</div>
                ) : callLog.length ? callLog.slice(0, 9).map((c) => (
                  <div className="card" key={c.call_id + '-' + c.friend.id}>
                    <div className="person-top">
                      <div className="person-avatar">{initials(c.friend.nickname || c.friend.username)}</div>
                      <div>
                        <div className="person-name">{c.friend.nickname || c.friend.username}</div>
                        <div className="person-role">{relativeDay(c.created_at)}</div>
                      </div>
                    </div>
                    <span className={'badge' + (c.outgoing ? ' blue' : ' green')}>{c.outgoing ? 'Outgoing' : 'Incoming'}</span>
                    <p className="card-sub">
                      {c.ended_at ? `${Math.max(1, Math.round((new Date(c.ended_at) - new Date(c.created_at)) / 1000))}s` : 'Not answered'}
                    </p>
                  </div>
                )) : <div className="card-sub">No calls yet.</div>}
              </div>
            </section>

            {/* ========================= INSIGHTS ========================= */}
            <section className={screenClass('insights')}>
              <div className="page-heading">
                <div>
                  <h1>Insights</h1>
                  <p>Your weekly cognitive digest.</p>
                </div>
              </div>

              <div className="grid-3">
                <div className="card insight-card">
                  <div className="insight-icon">◒</div>
                  <div className="card-title">Mood</div>
                  <h2>{moodDays.length ? (isGoodMood(moodDays[moodDays.length - 1].mood_label) ? 'Mostly positive' : isNeutralMood(moodDays[moodDays.length - 1].mood_label) ? 'Steady' : 'Could be better') : 'No data yet'}</h2>
                  <div className="card-sub">{moodDays.length ? `${moodDays.length} day${moodDays.length === 1 ? '' : 's'} logged in the last 56.` : 'Chat or record conversations to start tracking mood.'}</div>
                </div>
                <div className="card">
                  <div className="card-title">Relationships</div>
                  <h2>{friends.length} friend{friends.length === 1 ? '' : 's'}</h2>
                  <p className="card-sub">{peopleCount} people total across friends and recordings.</p>
                </div>
                <div className="card">
                  <div className="card-title">Weekly digest</div>
                  <h2>{digest ? `${digest.length} insight${digest.length === 1 ? '' : 's'}` : 'None yet'}</h2>
                  <p className="card-sub">{digest ? 'Generated from this week\'s activity.' : 'Digests are generated automatically as you use the app.'}</p>
                </div>
              </div>

              <div className="grid-2" style={{ marginTop: 20 }}>
                <div className="card">
                  <h3>Mood trend (7 days)</h3>
                  <div className="chart">
                    {trendCells.map((d, i) => (
                      <div key={i} className="bar" style={{ height: (d ? moodHeight(d.mood_label) : 6) + '%', opacity: d ? 1 : 0.25 }} title={d ? `${d.date}: ${d.mood_label}` : 'No data'} />
                    ))}
                  </div>
                </div>

                <div className="card">
                  <h3>Mood calendar (56 days)</h3>
                  <div className="heatmap">
                    {heatmapCells.map((d, i) => (
                      <div
                        key={i}
                        className="heat"
                        title={d ? `${d.date}: ${d.mood_label}` : 'No data'}
                        style={{ background: d ? (isGoodMood(d.mood_label) ? '#2dd4bf' : isNeutralMood(d.mood_label) ? '#99f6e4' : '#d1fae5') : 'var(--surface-2)' }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            </section>

            {/* ========================= ASK ========================= */}
            {/* A real scrolling thread (history above, composer pinned at
                the bottom) instead of a search-box-with-one-reply-below --
                matches how Conversations/Listen already behave, and how a
                chat surface is generally expected to work. */}
            <section className={screenClass('ask')}>
              <div className="page-heading">
                <div>
                  <h1>Ask Throughline</h1>
                  <p>Ask questions across your conversations, people, tasks and memories.</p>
                </div>
              </div>

              <div className="chat-window ask-thread">
                <div className="chat-header">
                  <div className="avatar">✦</div>
                  <div>
                    <div className="item-title">Ask Throughline</div>
                    <div className="item-meta">Ask across everything you've recorded</div>
                  </div>
                </div>
                <div className="messages" ref={askMessagesRef}>
                  {askHistory === null ? (
                    <div className="item-meta">Loading…</div>
                  ) : askHistory.length === 0 ? (
                    <div className="ask-empty">
                      <div className="ai-pill" style={{ display: 'flex', width: 'max-content', margin: '0 auto 18px' }}>
                        ✦ Cognitive intelligence
                      </div>
                      <div className="suggestions">
                        <div className="suggestion" onClick={() => fillAsk('What commitments have I made?')}>
                          What commitments have I made?
                        </div>
                        <div className="suggestion" onClick={() => fillAsk('Who have I not spoken to recently?')}>
                          Who have I not spoken to recently?
                        </div>
                        <div className="suggestion" onClick={() => fillAsk('Summarize my week.')}>
                          Summarize my week.
                        </div>
                      </div>
                    </div>
                  ) : (
                    askHistory.map((m, i) => (
                      <div key={i} className={'message' + (m.role === 'user' ? ' me' : '')}>
                        {m.imageUrl && <img src={m.imageUrl} alt="Attached" className="ask-reply-image" />}
                        {m.content}
                      </div>
                    ))
                  )}
                  {askLoading && <div className="message">Thinking…</div>}
                </div>

                {askImagePreview && (
                  <div className="ask-attachment-preview">
                    <img src={askImagePreview} alt="Attached" />
                    <span>Image attached -- type a question about it and hit Ask.</span>
                    <button type="button" className="icon-button" title="Remove image" onClick={removeAskImage}><CloseIcon /></button>
                  </div>
                )}

                <div className="chat-input pill-input">
                  <input
                    ref={askImageInputRef} type="file" accept="image/*" style={{ display: 'none' }}
                    onChange={onAskImageSelected}
                  />
                  <button
                    type="button" className="round-icon-btn" title="Attach an image"
                    onClick={pickAskImage} disabled={askLoading}
                  >
                    <AttachIcon />
                  </button>
                  <input
                    placeholder={askImageFile ? 'Ask something about this image...' : 'Ask about this conversation...'}
                    value={askInput}
                    onChange={(e) => setAskInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && askThroughline()}
                  />
                  <button
                    type="button" className="round-icon-btn send" title="Ask"
                    disabled={askLoading} onClick={askThroughline}
                  >
                    <SendIcon />
                  </button>
                </div>
              </div>
            </section>

            {/* ========================= SHARING ========================= */}
            <section className={screenClass('sharing')}>
              <div className="page-heading">
                <div>
                  <h1>Cognitive Sharing</h1>
                  <p>Choose what intelligence you share with your friends.</p>
                </div>
              </div>

              <div className="card">
                {friends.length === 0 && <p className="card-sub">Add friends to configure sharing.</p>}
                {friends.map((f) => {
                  const s = sharingMap[f.id];
                  return (
                    <div className="settings-row" key={f.id}>
                      <div>
                        <div className="settings-label">{f.nickname || f.username}</div>
                        <div className="settings-description">
                          {s ? (s.both_enabled ? 'Mutual suggestions enabled' : 'Waiting on the other side to opt in') : 'Loading…'}
                        </div>
                      </div>
                      <select
                        className="context-select"
                        value={s?.my_level || 'off'}
                        disabled={!s || sharingSavingId === f.id}
                        onChange={(e) => setFriendSharing(f.id, e.target.value)}
                      >
                        {SHARING_LEVELS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                      </select>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* ========================= COMMERCE ========================= */}
            <section className={screenClass('commerce')}>
              <div className="page-heading">
                <div>
                  <h1>Cognitive Commerce</h1>
                  <p>Ask Throughline for something and review before ordering.</p>
                </div>
              </div>

              {swiggy === null ? (
                <div className="card"><p className="card-sub">Loading…</p></div>
              ) : !swiggy.enabled ? (
                <div className="card"><p className="card-sub">Cognitive Commerce isn't enabled on this account yet.</p></div>
              ) : (
                <div className="card">
                  <div className="ai-pill">{swiggy.accounts?.food?.connected ? '✦ Connected' : '✦ Not connected'}</div>
                  <h2>Order food from inside a chat</h2>
                  <p className="card-sub">
                    Real ordering happens through <b>Ask Throughline</b> or any conversation -- ask for
                    food and Throughline will search real options, show you a pick-an-item card, and only
                    places an order after you explicitly confirm. Nothing here orders anything on its own.
                  </p>
                  <div style={{ marginTop: 18, display: 'flex', gap: 10 }}>
                    {swiggy.accounts?.food?.connected ? (
                      <button className="secondary-button" onClick={() => disconnectSwiggy('food')}>Disconnect Swiggy</button>
                    ) : (
                      <a className="primary-button" style={{ textDecoration: 'none', display: 'inline-block' }} href="/integrations/swiggy/connect?server=food">
                        Connect Swiggy
                      </a>
                    )}
                    <button className="secondary-button" onClick={() => go('ask')}>Try it in Ask →</button>
                  </div>
                </div>
              )}
            </section>

            {/* ========================= NOTIFICATIONS ========================= */}
            <section className={screenClass('notifications')}>
              <div className="page-heading">
                <div>
                  <h1>Notifications</h1>
                  <p>Everything Throughline thinks you should know.</p>
                </div>
              </div>

              <div className="card">
                <div className="list">
                  {derivedNotifications.length ? derivedNotifications.map((n) => (
                    <div className="list-item" key={n.id}>
                      <div className="avatar">{n.avatar}</div>
                      <div className="item-main">
                        <div className="item-title">{n.title}</div>
                        <div className="item-meta">{n.meta}</div>
                      </div>
                      {n.badgeLabel && <span className={'badge' + (n.badge ? ' ' + n.badge : '')}>{n.badgeLabel}</span>}
                    </div>
                  )) : <div className="item-meta" style={{ padding: 12 }}>You're all caught up.</div>}
                </div>
              </div>
            </section>

            {/* ========================= SETTINGS ========================= */}
            <section className={screenClass('settings')}>
              <div className="page-heading">
                <div>
                  <h1>Settings</h1>
                  <p>Control how Throughline understands and assists you.</p>
                </div>
              </div>

              <div className="card" style={{ textAlign: 'center' }}>
                <h3>Profile picture</h3>
                {uploadsEnabled ? (
                  <>
                    <input
                      ref={pictureInputRef} type="file" accept="image/jpeg,image/png,image/webp"
                      style={{ display: 'none' }} onChange={onProfilePictureSelected}
                    />
                    <div style={{ position: 'relative', width: 'fit-content', margin: '8px auto' }}>
                      <button
                        title={settings?.profile_picture_url ? 'View profile picture' : 'Add a profile picture'}
                        onClick={settings?.profile_picture_url ? () => setViewingOwnPhoto(true) : pickProfilePicture}
                        disabled={uploadingPicture}
                        style={{ display: 'block', opacity: uploadingPicture ? 0.6 : 1, border: 0, background: 'none', padding: 0 }}
                      >
                        <Avatar url={settings?.profile_picture_url} name={user?.username} size="xl" />
                      </button>
                      <button
                        className="avatar-camera-badge" title="Change profile picture"
                        onClick={pickProfilePicture} disabled={uploadingPicture}
                      >
                        {uploadingPicture ? <span className="spinner-sm" /> : <CameraIcon />}
                      </button>
                    </div>
                    <p className="card-sub">
                      {uploadingPicture ? 'Uploading...' : settings?.profile_picture_url ? 'Tap to view · camera to change' : 'Tap to add a photo'}
                    </p>
                  </>
                ) : <p className="card-sub">Photo uploads aren't enabled on this account yet.</p>}
                {viewingOwnPhoto && (
                  <PhotoViewer url={settings?.profile_picture_url} name={user?.username} onClose={() => setViewingOwnPhoto(false)} />
                )}
              </div>

              <div className="card" style={{ marginTop: 20 }}>
                <h3>Personalization</h3>

                <div className="settings-row">
                  <div>
                    <div className="settings-label">Smart features</div>
                    <div className="settings-description">Master switch for nudges, cognitive intelligence, reminders and live tags.</div>
                  </div>
                  <Toggle on={!!nudgeSettings?.smart_features_enabled} onChange={(v) => updateNudge('smart_features_enabled', v)} />
                </div>

                <div className="settings-row">
                  <div>
                    <div className="settings-label">Proactive nudges</div>
                    <div className="settings-description">Let Throughline notify you about important changes.</div>
                  </div>
                  <Toggle on={!!nudgeSettings?.nudges_enabled} disabled={!nudgeSettings?.smart_features_enabled} onChange={(v) => updateNudge('nudges_enabled', v)} />
                </div>

                <div className="settings-row">
                  <div>
                    <div className="settings-label">Cognitive suggestions</div>
                    <div className="settings-description">Surface relevant context proactively.</div>
                  </div>
                  <Toggle on={!!nudgeSettings?.cognitive_intelligence_enabled} disabled={!nudgeSettings?.smart_features_enabled} onChange={(v) => updateNudge('cognitive_intelligence_enabled', v)} />
                </div>

                <div className="settings-row">
                  <div>
                    <div className="settings-label">Task reminder notifications</div>
                    <div className="settings-description">Get reminded before a task is due.</div>
                  </div>
                  <Toggle on={!!nudgeSettings?.task_reminder_notifications_enabled} disabled={!nudgeSettings?.smart_features_enabled} onChange={(v) => updateNudge('task_reminder_notifications_enabled', v)} />
                </div>

                <div className="settings-row">
                  <div>
                    <div className="settings-label">Tags &amp; questions while listening</div>
                    <div className="settings-description">Show topic chips and follow-up questions during a live session.</div>
                  </div>
                  <Toggle on={!!nudgeSettings?.tags_questions_enabled} disabled={!nudgeSettings?.smart_features_enabled} onChange={(v) => updateNudge('tags_questions_enabled', v)} />
                </div>

                <div className="settings-row">
                  <div>
                    <div className="settings-label">Shared attachment summaries</div>
                    <div className="settings-description">AI-generated summaries of files shared with friends who have Cognitive Sharing on.</div>
                  </div>
                  <DecorativeToggle defaultOn />
                </div>

                <div className="settings-row">
                  <div>
                    <div className="settings-label">Default mode</div>
                    <div className="settings-description">Personalization context used across the app.</div>
                  </div>
                  <select
                    className="context-select"
                    value={settings?.personalization || 'personal'}
                    onChange={(e) => updatePersonalization(e.target.value)}
                  >
                    <option value="personal">Personal</option>
                    <option value="office">Office</option>
                    <option value="study">Study</option>
                  </select>
                </div>

                <div className="settings-row" style={{ borderBottom: 0 }}>
                  <div>
                    <div className="settings-label">Default mode is also used for</div>
                    <div className="settings-description">Categorizing new conversations, tasks, and chat tone across the app.</div>
                  </div>
                </div>
              </div>

              <div className="card" style={{ marginTop: 20 }}>
                <h3>Appearance</h3>
                <div style={{ display: 'flex', gap: 10, marginTop: 12 }}>
                  <button
                    className={theme === 'light' ? 'primary-button' : 'secondary-button'}
                    style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
                    onClick={() => theme !== 'light' && toggleTheme()}
                  >
                    <SunIcon /> Light
                  </button>
                  <button
                    className={theme === 'dark' ? 'primary-button' : 'secondary-button'}
                    style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
                    onClick={() => theme !== 'dark' && toggleTheme()}
                  >
                    <MoonIcon /> Dark
                  </button>
                </div>
              </div>

              <div className="card" style={{ marginTop: 20 }}>
                <h3>Your categories</h3>
                <p className="card-sub">personal/office/study always exist -- add your own on top.</p>
                {categories.custom.map((c) => (
                  <div className="settings-row" key={c}>
                    <span>{c}</span>
                    <button className="icon-button" title="Delete category" onClick={() => removeCategory(c)}><TrashIcon /></button>
                  </div>
                ))}
                <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                  <input
                    className="chat-search" style={{ margin: 0 }} placeholder="New category name"
                    value={newCategory} onChange={(e) => setNewCategory(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && addCategory()}
                  />
                  <button className="primary-button" onClick={addCategory}>Add</button>
                </div>
                {categoryError && <p className="card-sub" style={{ color: 'var(--danger)' }}>{categoryError}</p>}
              </div>

              <div className="card" style={{ marginTop: 20 }}>
                <h3>Your friend code</h3>
                <div className="card-value" style={{ fontFamily: 'monospace', fontSize: 22 }}>{settings?.friend_code || '…'}</div>
                <p className="card-sub">Share this so a friend can add you from People.</p>
              </div>

              {swiggy?.enabled && (
                <div className="card" style={{ marginTop: 20 }}>
                  <h3>Swiggy</h3>
                  <p className="card-sub">Connect your account so the assistant can suggest real options and order for you when asked.</p>
                  {Object.entries(SWIGGY_SERVER_LABELS).map(([server, label]) => {
                    const account = swiggy.accounts?.[server];
                    return (
                      <div className="settings-row" key={server}>
                        <span>{label}</span>
                        {account?.connected ? (
                          <button className="secondary-button" onClick={() => disconnectSwiggy(server)}>Disconnect</button>
                        ) : (
                          <a className="primary-button" style={{ textDecoration: 'none' }} href={`/integrations/swiggy/connect?server=${server}`}>Connect</a>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              <button className="secondary-button" style={{ marginTop: 20 }} onClick={auth.logout}>Log out</button>
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}

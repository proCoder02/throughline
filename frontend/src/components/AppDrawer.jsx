import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJson } from '../api.js';
import {
  HomeIcon, MeetIcon, ChatIcon, TaskIcon, ProfileIcon, FriendIcon, SettingsIcon, ClockIcon, CloseIcon,
} from '../icons.jsx';
import { NAV_ITEMS } from '../lib/nav.js';

const ICONS = { home: HomeIcon, chats: ChatIcon, tasks: TaskIcon, friends: FriendIcon, profiles: ProfileIcon, meet: MeetIcon, settings: SettingsIcon };

// Web counterpart of the Flutter client's _AppDrawer (home_shell.dart) --
// same destinations (Home/Chats/Tasks/Profiles/Friends/Settings, same badge
// counts), same warm-gradient styling, and the same "Recent Conversations"
// section below it, ChatGPT-sidebar style. Phone-only (see App.jsx) --
// desktop keeps its existing IconRail, unchanged.
export default function AppDrawer({ open, onClose, active, notify, onOpenConversation }) {
  const navigate = useNavigate();
  const [recent, setRecent] = useState(null); // null = loading

  useEffect(() => {
    if (!open || recent !== null) return;
    apiJson('/conversations').then((d) => setRecent((d || []).slice(0, 12))).catch(() => setRecent([]));
  }, [open, recent]);

  const badges = { chats: notify?.unreadChatIds?.size || 0, tasks: notify?.taskCount || 0 };
  const items = NAV_ITEMS.map(({ key, label }) => ({ key, label, Icon: ICONS[key], badge: badges[key] || 0 }));

  const select = (key) => {
    onClose();
    navigate('/app/' + key);
  };

  const openConversation = (id) => {
    onClose();
    onOpenConversation(id);
  };

  return (
    <>
      <div className={'app-drawer-overlay' + (open ? ' open' : '')} onClick={onClose} aria-hidden={!open} />
      <nav className={'app-drawer' + (open ? ' open' : '')} aria-label="Main" aria-hidden={!open}>
        <button className="app-drawer-close" title="Close menu" aria-label="Close menu" onClick={onClose}>
          <CloseIcon />
        </button>
        <div className="app-drawer-items">
          {items.map(({ key, label, Icon, badge }) => (
            <button
              key={key}
              className={'app-drawer-item' + (active === key ? ' active' : '')}
              onClick={() => select(key)}
            >
              <Icon />
              <span>{label}</span>
              {badge > 0 && <span className="app-drawer-badge">{badge > 9 ? '9+' : badge}</span>}
            </button>
          ))}
        </div>

        <div className="app-drawer-section-title">
          <ClockIcon /> Recent Conversations
        </div>
        <div className="app-drawer-recent">
          {recent === null ? (
            <div className="hint" style={{ padding: '4px 16px' }}>Loading...</div>
          ) : recent.length === 0 ? (
            <div className="hint" style={{ padding: '4px 16px' }}>Nothing recorded yet.</div>
          ) : (
            recent.map((c) => (
              <button key={c.id} className="app-drawer-recent-item" onClick={() => openConversation(c.id)}>
                <ChatIcon />
                <span>{c.title || 'Untitled conversation'}</span>
              </button>
            ))
          )}
        </div>
      </nav>
    </>
  );
}

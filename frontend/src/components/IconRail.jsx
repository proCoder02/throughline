import Avatar from './Avatar.jsx';
import { ChatIcon, TaskIcon, ProfileIcon, FriendIcon, SettingsIcon } from '../icons.jsx';

const SECTIONS = [
  { key: 'chats', label: 'Chats', Icon: ChatIcon },
  { key: 'tasks', label: 'Tasks', Icon: TaskIcon },
  { key: 'profiles', label: 'Profiles', Icon: ProfileIcon },
  { key: 'friends', label: 'Friends', Icon: FriendIcon },
];

export default function IconRail({ active, onSelect, username, profilePictureUrl, badges = {}, online }) {
  return (
    <nav className="icon-rail" aria-label="Main">
      {SECTIONS.map(({ key, label, Icon }) => {
        const count = badges[key] || 0;
        return (
          <button
            key={key}
            className={'rail-btn' + (active === key ? ' active' : '')}
            title={label}
            aria-label={count > 0 ? `${label} (${count} unread)` : label}
            aria-current={active === key ? 'page' : undefined}
            onClick={() => onSelect(key)}
          >
            <Icon />
            <span className="rail-label" aria-hidden="true">{label}</span>
            {count > 0 && <span className="rail-badge" aria-hidden="true">{count > 9 ? '9+' : count}</span>}
          </button>
        );
      })}
      <div className="rail-spacer" />
      <button
        className={'rail-btn' + (active === 'settings' ? ' active' : '')}
        title="Settings"
        aria-label="Settings"
        aria-current={active === 'settings' ? 'page' : undefined}
        onClick={() => onSelect('settings')}
      >
        <SettingsIcon />
        <span className="rail-label" aria-hidden="true">Settings</span>
      </button>
      <button
        className="rail-btn avatar-btn"
        title={`${username || ''} -- ${online ? 'Online' : 'Reconnecting...'}`}
        aria-label={`${username || 'Account'}, ${online ? 'online' : 'reconnecting'} -- open Settings`}
        onClick={() => onSelect('settings')}
      >
        <Avatar url={profilePictureUrl} name={username} />
        <span className={'presence-dot' + (online ? '' : ' offline')} aria-hidden="true" />
      </button>
    </nav>
  );
}

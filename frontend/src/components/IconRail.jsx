import { ChatIcon, TaskIcon, ProfileIcon, FriendIcon, SettingsIcon } from '../icons.jsx';

const SECTIONS = [
  { key: 'chats', label: 'Chats', Icon: ChatIcon },
  { key: 'tasks', label: 'Tasks', Icon: TaskIcon },
  { key: 'profiles', label: 'Profiles', Icon: ProfileIcon },
  { key: 'friends', label: 'Friends', Icon: FriendIcon },
];

export default function IconRail({ active, onSelect, username, badges = {} }) {
  return (
    <nav className="icon-rail">
      {SECTIONS.map(({ key, label, Icon }) => {
        const count = badges[key] || 0;
        return (
          <button
            key={key}
            className={'rail-btn' + (active === key ? ' active' : '')}
            title={label}
            onClick={() => onSelect(key)}
          >
            <Icon />
            {count > 0 && <span className="rail-badge">{count > 9 ? '9+' : count}</span>}
          </button>
        );
      })}
      <div className="rail-spacer" />
      <button
        className={'rail-btn' + (active === 'settings' ? ' active' : '')}
        title="Settings"
        onClick={() => onSelect('settings')}
      >
        <SettingsIcon />
      </button>
      <button className="rail-btn avatar-btn" title={username} onClick={() => onSelect('settings')}>
        <span className="avatar">{(username || '?')[0]}</span>
      </button>
    </nav>
  );
}

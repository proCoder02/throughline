import Avatar from './Avatar.jsx';
import { HomeIcon, ChatIcon, TaskIcon, ProfileIcon, FriendIcon, MeetIcon, SettingsIcon } from '../icons.jsx';
import { NAV_ITEMS } from '../lib/nav.js';
import ModeSwitch from './ModeSwitch.jsx';

const ICONS = { home: HomeIcon, chats: ChatIcon, tasks: TaskIcon, friends: FriendIcon, profiles: ProfileIcon, meet: MeetIcon };
// Desktop's Home is the full "os" screen (see lib/nav.js homePath).
const ROUTE = { home: 'os' };

export default function IconRail({ active, onSelect, username, profilePictureUrl, badges = {}, online }) {
  const sections = NAV_ITEMS.filter((i) => i.key !== 'settings');
  return (
    <nav className="icon-rail" aria-label="Main">
      {sections.map(({ key, label }) => {
        const Icon = ICONS[key];
        const count = badges[key] || 0;
        return (
          <button
            key={key}
            className={'rail-btn' + (active === key ? ' active' : '')}
            title={label}
            aria-label={count > 0 ? `${label} (${count} unread)` : label}
            aria-current={active === key ? 'page' : undefined}
            onClick={() => onSelect(ROUTE[key] || key)}
          >
            <Icon />
            <span className="rail-label" aria-hidden="true">{label}</span>
            {count > 0 && <span className="rail-badge" aria-hidden="true">{count > 9 ? '9+' : count}</span>}
          </button>
        );
      })}
      <div className="rail-spacer" />
      <ModeSwitch current="personal" variant="rail" />
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

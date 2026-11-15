// Shown once per device (when this device has never picked a side): the two
// ways to use Throughline. After that the app opens on the side last used,
// and the switch in the navigation changes it.
import { useEffect, useState } from 'react';
import { BuildingIcon, HomeIcon } from '../icons.jsx';
import { fetchMode } from '../lib/mode.js';
import { useSwitchMode } from './ModeSwitch.jsx';
import '../styles/mode.css';

const OPTIONS = [
  {
    mode: 'personal',
    Icon: HomeIcon,
    title: 'Personal',
    text: 'Your conversations, people, tasks and reminders. Everything you use today.',
  },
  {
    mode: 'corporate',
    Icon: BuildingIcon,
    title: 'Corporate',
    text: 'Your organisation: departments, teams and colleagues, with shared memory of meetings and decisions.',
  },
];

export default function ModeChooser({ username }) {
  const { go, busy } = useSwitchMode();
  const [current, setCurrent] = useState(null);
  useEffect(() => { fetchMode().then(setCurrent).catch(() => {}); }, []);

  return (
    <main className="mode-chooser">
      <div className="mode-chooser-inner">
        <h1>How do you want to use Throughline{username ? `, ${username}` : ''}?</h1>
        <p className="mode-chooser-sub">You can switch any time from the menu.</p>
        <div className="mode-chooser-options">
          {OPTIONS.map(({ mode, Icon, title, text }) => (
            <button key={mode} className={'mode-card' + (current === mode ? ' current' : '')}
              onClick={() => go(mode)} disabled={busy}>
              <span className="mode-card-icon" aria-hidden="true"><Icon /></span>
              <span className="mode-card-title">{title}</span>
              <span className="mode-card-text">{text}</span>
              {current === mode && <span className="mode-card-badge">Last used</span>}
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}

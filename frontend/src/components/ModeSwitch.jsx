// The Personal / Corporate switch. Three looks for the three places it sits:
//   segmented -- a two-option control (OS sidebar, Corporate sidebar)
//   rail      -- one icon button in the desktop icon rail
//   drawer    -- one row in the phone drawer
// Every variant does the same thing: saves the mode (lib/mode.js) and goes
// to that side's home screen.
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { switchMode, modeHome } from '../lib/mode.js';
import { showToast } from '../lib/notify.js';
import { useIsPhone } from '../hooks/useIsPhone.js';
import { BuildingIcon, HomeIcon } from '../icons.jsx';
import { useCorpUnread } from '../lib/corpLive.js';
import '../styles/mode.css';

export function useSwitchMode() {
  const navigate = useNavigate();
  const isPhone = useIsPhone();
  const [busy, setBusy] = useState(false);
  const go = async (mode) => {
    if (busy) return;
    setBusy(true);
    try {
      await switchMode(mode);
      navigate(modeHome(mode, isPhone));
    } catch (e) {
      showToast('Couldn’t switch: ' + (e.message || 'please try again.'), 'error');
    } finally {
      setBusy(false);
    }
  };
  return { go, busy };
}

export default function ModeSwitch({ current, variant = 'segmented', onDone }) {
  const { go, busy } = useSwitchMode();
  // Unread work messages show on the Corporate side of the switch while in Personal.
  const { total } = useCorpUnread();
  const badge = current === 'personal' && total > 0 ? (total > 9 ? '9+' : String(total)) : null;
  const other = current === 'corporate' ? 'personal' : 'corporate';
  const pick = async (mode) => {
    if (mode === current) return;
    await go(mode);
    onDone?.();
  };

  if (variant === 'rail' || variant === 'drawer') {
    const Icon = other === 'corporate' ? BuildingIcon : HomeIcon;
    const label = other === 'corporate' ? 'Corporate' : 'Personal';
    if (variant === 'drawer') {
      return (
        <button className="app-drawer-item mode-drawer-item" onClick={() => pick(other)} disabled={busy}>
          <Icon />
          <span>Switch to {label}</span>
          {badge && <span className="app-drawer-badge">{badge}</span>}
        </button>
      );
    }
    return (
      <button className="rail-btn" title={`Switch to ${label}`} aria-label={`Switch to ${label}`}
        onClick={() => pick(other)} disabled={busy}>
        <Icon />
        <span className="rail-label" aria-hidden="true">{label}</span>
        {badge && <span className="rail-badge" aria-hidden="true">{badge}</span>}
      </button>
    );
  }

  return (
    <div className="mode-switch" role="group" aria-label="Personal or Corporate">
      {['personal', 'corporate'].map((m) => (
        <button
          key={m}
          className={'mode-switch-btn' + (m === current ? ' active' : '')}
          aria-pressed={m === current}
          disabled={busy}
          onClick={() => pick(m)}
        >
          {m === 'personal' ? 'Personal' : 'Corporate'}
          {m === 'corporate' && badge && <span className="mode-badge" aria-label={`${total} unread work messages`}>{badge}</span>}
        </button>
      ))}
    </div>
  );
}

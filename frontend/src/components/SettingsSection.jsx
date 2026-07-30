import { useEffect, useState } from 'react';
import { apiJson, post } from '../api.js';

export default function SettingsSection({ user, onLogout }) {
  const [settings, setSettings] = useState(null);

  useEffect(() => { apiJson('/settings').then(setSettings).catch(() => {}); }, []);

  const changeMode = async (personalization) => {
    setSettings((s) => ({ ...s, personalization }));
    await post('/settings', { personalization });
  };

  return (
    <div className="detail-pane" style={{ flex: 1 }}>
      <div className="detail-card">
        <div className="detail-title">Signed in as {user?.username}</div>
        <label className="hint" style={{ display: 'block', marginTop: 12 }}>Personalisation mode</label>
        <select className="field" value={settings?.personalization || 'personal'} onChange={(e) => changeMode(e.target.value)}>
          <option value="personal">Personal</option>
          <option value="office">Office</option>
          <option value="study">Study</option>
        </select>
      </div>
      <div className="detail-card">
        <div className="detail-title">Your friend code</div>
        <div className="detail-meta" style={{ fontSize: 18, fontFamily: 'monospace' }}>{settings?.friend_code || '...'}</div>
        <p className="hint">Share this so a friend can add you from the Friends tab.</p>
      </div>
      <button className="btn secondary" onClick={onLogout}>Log out</button>
    </div>
  );
}

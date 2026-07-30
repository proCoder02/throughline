import { useEffect, useState } from 'react';
import ListPane from './ListPane.jsx';
import { ChatIcon } from '../icons.jsx';
import { apiJson } from '../api.js';

export default function ProfilesSection({ onOpenConversation }) {
  const [profiles, setProfiles] = useState({});
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState('');

  useEffect(() => { apiJson('/profiles').then(setProfiles).catch(() => {}); }, []);

  const labels = Object.keys(profiles).filter((l) => l.toLowerCase().includes(query.trim().toLowerCase()));

  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const fmt = (iso) => {
    const d = new Date(iso);
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  };

  // /profiles now reports categories + last_seen straight from the
  // profile_categories mapping table (a real many-to-many: one person can
  // span several categories, one category holds many people) instead of
  // this component re-deriving it from the notes list.
  return (
    <>
      <ListPane
        title="Profiles"
        search={{ value: query, onChange: setQuery, placeholder: 'Search profiles' }}
        emptyText={query ? 'No profiles match your search.' : 'No profiles yet. Analyze a conversation first.'}
      >
        {labels.map((label) => {
          const p = profiles[label];
          return (
            <div key={label} className={'row' + (label === selected ? ' active' : '')} onClick={() => setSelected(label)}>
              <span className="avatar">{label[0]}</span>
              <div className="row-main">
                <div className="row-top"><span className="row-title">{label}</span></div>
                <div className="row-sub">
                  {p.categories.map(cap).join(', ')}{p.last_seen && ` · ${fmt(p.last_seen)}`}
                </div>
              </div>
            </div>
          );
        })}
      </ListPane>
      <div className="detail-pane" style={{ flex: 1 }}>
        {selected ? (
          <div className="detail-card">
            <div className="detail-title">{selected}</div>
            {profiles[selected].notes.map((n, i) => (
              <div key={i} className="detail-meta detail-meta-row">
                <span><strong>{cap(n.category)}</strong> · {fmt(n.created_at)} — {n.observation}</span>
                {n.conversation_id && (
                  <button
                    className="conv-link-btn"
                    title="View source conversation"
                    onClick={() => onOpenConversation?.(n.conversation_id)}
                  >
                    <ChatIcon />
                  </button>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="hint">Select a profile to see observations.</div>
        )}
      </div>
    </>
  );
}

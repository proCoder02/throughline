import { useEffect, useState } from 'react';
import { TrashIcon } from '../icons.jsx';
import { apiJson, post, del } from '../api.js';

export default function SettingsSection({ user, onLogout }) {
  const [settings, setSettings] = useState(null);
  const [categories, setCategories] = useState({ builtin: [], custom: [] });
  const [newCategory, setNewCategory] = useState('');
  const [categoryError, setCategoryError] = useState('');

  const loadCategories = () => apiJson('/categories').then(setCategories).catch(() => {});

  useEffect(() => { apiJson('/settings').then(setSettings).catch(() => {}); }, []);
  useEffect(() => { loadCategories(); }, []);

  const changeMode = async (personalization) => {
    setSettings((s) => ({ ...s, personalization }));
    await post('/settings', { personalization });
  };

  const addCategory = async () => {
    const name = newCategory.trim();
    if (!name) return;
    setCategoryError('');
    try {
      await post('/categories', { name });
      setNewCategory('');
      loadCategories();
    } catch (e) {
      setCategoryError(e.message);
    }
  };

  const removeCategory = async (name) => {
    if (!confirm(`Delete your "${name}" category? Conversations already tagged with it keep the label.`)) return;
    await del(`/categories/${encodeURIComponent(name)}`);
    loadCategories();
  };

  return (
    <div className="detail-pane" style={{ flex: 1 }}>
      <div className="detail-card">
        <div className="detail-title">Signed in as {user?.username}</div>
        <label className="hint" style={{ display: 'block', marginTop: 12 }}>Personalisation mode</label>
        <select className="field" value={settings?.personalization || 'personal'} onChange={(e) => changeMode(e.target.value)}>
          {categories.builtin.map((c) => <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>)}
          {categories.custom.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div className="detail-card">
        <div className="detail-title">Your categories</div>
        <p className="hint">personal/office/study always exist -- add your own on top (e.g. "Family", "Side project").</p>
        {categories.custom.map((c) => (
          <div key={c} className="detail-meta-row" style={{ marginBottom: 6 }}>
            <span>{c}</span>
            <button className="conv-link-btn" title="Delete category" onClick={() => removeCategory(c)}>
              <TrashIcon />
            </button>
          </div>
        ))}
        <div className="row2" style={{ display: 'flex', gap: 6, marginTop: 8 }}>
          <input
            className="field" style={{ marginTop: 0 }} placeholder="New category name"
            value={newCategory} onChange={(e) => setNewCategory(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && addCategory()}
          />
          <button className="btn" onClick={addCategory}>Add</button>
        </div>
        {categoryError && <div className="auth-error">{categoryError}</div>}
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

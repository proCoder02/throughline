import { useEffect, useRef, useState } from 'react';
import Avatar from './Avatar.jsx';
import PhotoViewer from './PhotoViewer.jsx';
import { TrashIcon, SunIcon, MoonIcon, CameraIcon } from '../icons.jsx';
import { apiJson, post, del } from '../api.js';
import { confirmDialog, showToast } from '../lib/notify.js';
import { uploadFile, confirmUpload, getUploadsStatus } from '../lib/uploads.js';

const SWIGGY_SERVER_LABELS = { food: 'Swiggy Food', im: 'Swiggy Instamart', dineout: 'Swiggy Dineout' };

export default function SettingsSection({ user, onLogout, onUpdateUser, online, theme, onToggleTheme }) {
  const [settings, setSettings] = useState(null);
  const [categories, setCategories] = useState({ builtin: [], custom: [] });
  const [newCategory, setNewCategory] = useState('');
  const [categoryError, setCategoryError] = useState('');
  // Cognitive Commerce (Swiggy MCP) -- null while unknown, {enabled:false}
  // when SWIGGY_MCP_ENABLED is off in the backend's .env, in which case
  // this whole card renders as nothing (see below). Never assume enabled
  // while this hasn't resolved yet, so a deployment with the flag off never
  // flashes a connect button it can't actually do anything with.
  const [swiggyStatus, setSwiggyStatus] = useState(null);
  // Object storage (Cloudflare R2) -- same null-until-known/hide-if-disabled
  // pattern as swiggyStatus above (see GET /uploads/status).
  const [uploadsEnabled, setUploadsEnabled] = useState(false);
  const [uploadingPicture, setUploadingPicture] = useState(false);
  const pictureInputRef = useRef(null);
  // WhatsApp-style: tapping the photo itself views it full-screen; the
  // separate camera badge is what actually opens the file picker -- see
  // DirectMessageThread/FriendsSection's identical use of PhotoViewer for
  // a friend's photo.
  const [viewingOwnPhoto, setViewingOwnPhoto] = useState(false);

  const loadCategories = () => apiJson('/categories').then(setCategories).catch(() => {});
  const loadSwiggyStatus = () => apiJson('/integrations/swiggy/status').then(setSwiggyStatus).catch(() => setSwiggyStatus({ enabled: false }));

  useEffect(() => { apiJson('/settings').then(setSettings).catch(() => {}); }, []);
  useEffect(() => { loadCategories(); }, []);
  useEffect(() => { loadSwiggyStatus(); }, []);
  useEffect(() => { getUploadsStatus().then((s) => setUploadsEnabled(!!s.enabled)); }, []);

  const pickProfilePicture = () => pictureInputRef.current?.click();

  const onProfilePictureSelected = async (e) => {
    const file = e.target.files[0];
    e.target.value = ''; // allow picking the same file again later
    if (!file) return;
    setUploadingPicture(true);
    try {
      const objectKey = await uploadFile(file, 'profile_picture');
      const { profile_picture_url } = await post('/profile/picture', { object_key: objectKey });
      onUpdateUser?.({ profile_picture_url });
      setSettings((s) => ({ ...s, profile_picture_url }));
      showToast('Profile picture updated', 'success');
    } catch (err) {
      showToast('Could not update profile picture: ' + err.message, 'error');
    } finally {
      setUploadingPicture(false);
    }
  };

  const disconnectSwiggy = async (server) => {
    await post('/integrations/swiggy/disconnect', { server });
    loadSwiggyStatus();
  };

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
    const ok = await confirmDialog({
      title: `Delete "${name}"?`,
      message: 'Conversations already tagged with it keep the label.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    await del(`/categories/${encodeURIComponent(name)}`);
    loadCategories();
  };

  return (
    <div className="detail-pane" style={{ flex: 1 }}>
      <div className="detail-card">
        <div className="detail-title" style={{ display: 'flex', alignItems: 'center' }}>
          Signed in as {user?.username}
          <span className={'presence-dot inline' + (online ? '' : ' offline')} />
          <span className="hint" style={{ margin: '0 0 0 4px' }}>{online ? 'Online' : 'Reconnecting...'}</span>
        </div>
        <label className="hint" style={{ display: 'block', marginTop: 12 }}>Personalisation mode</label>
        <select className="field" value={settings?.personalization || 'personal'} onChange={(e) => changeMode(e.target.value)}>
          {categories.builtin.map((c) => <option key={c} value={c}>{c[0].toUpperCase() + c.slice(1)}</option>)}
          {categories.custom.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      {uploadsEnabled && (
        <div className="detail-card" style={{ textAlign: 'center' }}>
          <div className="detail-title">Profile picture</div>
          <input
            ref={pictureInputRef} type="file" accept="image/jpeg,image/png,image/webp"
            style={{ display: 'none' }} onChange={onProfilePictureSelected}
          />
          <div style={{ position: 'relative', width: 'fit-content', margin: '8px auto' }}>
            <button
              title={settings?.profile_picture_url ? 'View profile picture' : 'Add a profile picture'}
              aria-label={settings?.profile_picture_url ? 'View profile picture' : 'Add a profile picture'}
              onClick={settings?.profile_picture_url ? () => setViewingOwnPhoto(true) : pickProfilePicture}
              disabled={uploadingPicture}
              style={{ display: 'block', opacity: uploadingPicture ? 0.6 : 1 }}
            >
              <Avatar url={settings?.profile_picture_url} name={user?.username} size="xl" />
            </button>
            <button
              className="avatar-camera-badge" title="Change profile picture" aria-label="Change profile picture"
              onClick={pickProfilePicture} disabled={uploadingPicture}
            >
              {uploadingPicture ? <span className="spinner-sm" /> : <CameraIcon />}
            </button>
          </div>
          <p className="hint" style={{ marginTop: 0 }}>
            {uploadingPicture ? 'Uploading...' : settings?.profile_picture_url ? 'Tap to view · camera to change' : 'Tap to add a photo'}
          </p>
        </div>
      )}

      {viewingOwnPhoto && (
        <PhotoViewer
          url={settings?.profile_picture_url} name={user?.username}
          onClose={() => setViewingOwnPhoto(false)}
        />
      )}
      <div className="detail-card">
        <div className="detail-title">Appearance</div>
        <p className="hint">Moved here from the always-visible rail -- one less permanent button, still one tap away.</p>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className={'btn' + (theme === 'light' ? '' : ' secondary')}
            style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
            onClick={() => theme !== 'light' && onToggleTheme()}
          >
            <SunIcon /> Light
          </button>
          <button
            className={'btn' + (theme === 'dark' ? '' : ' secondary')}
            style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
            onClick={() => theme !== 'dark' && onToggleTheme()}
          >
            <MoonIcon /> Dark
          </button>
        </div>
      </div>
      <div className="detail-card">
        <div className="detail-title">Your categories</div>
        <p className="hint">personal/office/study always exist -- add your own on top (e.g. "Family", "Side project").</p>
        {categories.custom.map((c) => (
          <div key={c} className="detail-meta-row" style={{ marginBottom: 6 }}>
            <span>{c}</span>
            <button className="conv-link-btn" title="Delete category" aria-label={`Delete category ${c}`} onClick={() => removeCategory(c)}>
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
      {swiggyStatus?.enabled && (
        <div className="detail-card">
          <div className="detail-title">Swiggy</div>
          <p className="hint">Connect your Swiggy account so the assistant can suggest real options and order for you when you ask -- nothing is ever ordered without you confirming it first.</p>
          {Object.entries(SWIGGY_SERVER_LABELS).map(([server, label]) => {
            const account = swiggyStatus.accounts?.[server];
            return (
              <div key={server} className="detail-meta-row" style={{ marginBottom: 6 }}>
                <span>{label}</span>
                {account?.connected ? (
                  <button className="btn secondary" onClick={() => disconnectSwiggy(server)}>Disconnect</button>
                ) : (
                  <a className="btn" href={`/integrations/swiggy/connect?server=${server}`}>Connect</a>
                )}
              </div>
            );
          })}
        </div>
      )}
      <button className="btn secondary" onClick={onLogout}>Log out</button>
      {/* Required attribution -- the checkbox/loading animations (Tasks empty
          state, Friends loading spinners) are CC-BY via react-useanimations. */}
      <p className="hint" style={{ textAlign: 'center', marginTop: 16 }}>
        Some animations by <a href="https://useanimations.com" target="_blank" rel="noreferrer">useAnimations.com</a>
      </p>
    </div>
  );
}

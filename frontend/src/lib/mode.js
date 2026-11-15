// Personal vs Corporate -- which side of the website the user is on.
//
// For now this rides on the existing personalization setting
// (users.personalization, the same value that tags new recordings with a
// category): 'office' = Corporate; personal, study and custom categories =
// Personal. So a recording made while on the Corporate side is filed as an
// office conversation with no extra plumbing.
//
// Switching back to Personal restores whichever personal-side category the
// user last had (study, a custom one...), not always 'personal'.
import { apiJson, post } from '../api.js';

export const CORPORATE_CATEGORY = 'office';
const MODE_KEY = 'tl_mode';                        // last mode on this device, for the instant landing redirect
const PERSONAL_CATEGORY_KEY = 'tl_personal_category';

export const modeOfCategory = (category) => (category === CORPORATE_CATEGORY ? 'corporate' : 'personal');

function read(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function write(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private mode -- the server still has it */ }
}

// null when this device has never picked (the chooser is shown once then).
export const cachedMode = () => {
  const m = read(MODE_KEY);
  return m === 'corporate' || m === 'personal' ? m : null;
};

export const modeHome = (mode, isPhone) => (mode === 'corporate' ? '/app/corp' : isPhone ? '/app/home' : '/app/os');

// The server's answer wins over this device's cache (it may have been
// changed on another device or in Settings).
export async function fetchMode() {
  const s = await apiJson('/settings');
  const mode = modeOfCategory(s.personalization);
  if (mode === 'personal') write(PERSONAL_CATEGORY_KEY, s.personalization);
  write(MODE_KEY, mode);
  return mode;
}

export async function switchMode(mode) {
  let category = CORPORATE_CATEGORY;
  if (mode === 'personal') {
    category = read(PERSONAL_CATEGORY_KEY) || 'personal';
    if (category === CORPORATE_CATEGORY) category = 'personal';
  } else {
    // Remember where to come back to.
    const s = await apiJson('/settings').catch(() => null);
    if (s && modeOfCategory(s.personalization) === 'personal') write(PERSONAL_CATEGORY_KEY, s.personalization);
  }
  try {
    await post('/settings', { personalization: category });
  } catch (e) {
    // A remembered custom category that has since been deleted.
    if (mode !== 'personal' || category === 'personal') throw e;
    await post('/settings', { personalization: 'personal' });
    write(PERSONAL_CATEGORY_KEY, 'personal');
  }
  write(MODE_KEY, mode);
  return mode;
}

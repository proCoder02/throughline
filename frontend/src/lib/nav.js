// One list of the app's sections and what they're called, used by the
// desktop icon rail, the phone drawer and the phone top bar -- so the same
// place always has the same name and order everywhere.
//
// Route keys stay as they were ('chats', 'friends', 'profiles'): they're
// URLs people may have bookmarked, and the backend's own API owns the bare
// paths. Only the visible labels changed.
export const NAV_ITEMS = [
  { key: 'home', label: 'Home' },
  { key: 'chats', label: 'Conversations' },
  { key: 'tasks', label: 'Tasks' },
  { key: 'friends', label: 'Friends' },
  { key: 'profiles', label: 'People' },
  { key: 'meet', label: 'Meet' },
  { key: 'settings', label: 'Settings' },
];

export const SECTION_TITLES = Object.fromEntries(NAV_ITEMS.map((i) => [i.key, i.label]));
SECTION_TITLES.os = 'Home';

// Desktop's home is the full "os" screen; phone's is the Home section.
export const homePath = (isPhone) => (isPhone ? '/app/home' : '/app/os');

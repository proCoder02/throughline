// Best-effort, silent, and fast on purpose -- must never turn a normal text
// message into a permission-prompt interruption or a multi-second stall.
// Only actually asks the browser for location when the message looks like
// it wants one at all; every other message skips this with zero delay or
// prompt. A denied/unavailable/slow fix just means the backend gets no
// lat/lon and asks the user to share location or name a place instead --
// mirrors the Flutter client's _maybeGetLocation exactly.
const LOCATION_KEYWORD_RE = /\bnear(?:by)?\b|\baround\s+(?:here|me)\b|\bclose\s+to\s+me\b|\bnearest\b|\btrek\b|\bhik(?:e|ing)\b|\btrail\b|\bshop(?:ping)?\b|\brestaurant\b|\bcafe\b|\bcoffee\b|\bpark\b/i;

export function maybeGetLocation(text) {
  return new Promise((resolve) => {
    if (!LOCATION_KEYWORD_RE.test(text) || !navigator.geolocation) {
      resolve(null);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      () => resolve(null), // denied, unavailable, or timed out -- send proceeds without location
      { enableHighAccuracy: false, timeout: 4000, maximumAge: 60000 },
    );
  });
}

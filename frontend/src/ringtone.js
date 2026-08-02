// Synthesized ring (no audio asset to source/ship) -- a plain two-tone
// beep pattern repeated on an interval, standard "ring-ring... pause"
// cadence. Module-level state (not a hook) since only one ringtone should
// ever play at a time regardless of which component triggers it.
//
// iOS Safari only allows an AudioContext to produce sound if it was
// created/resumed during a real user gesture (click/tap) -- a WebSocket
// push (an incoming call, with zero preceding gesture in that moment)
// doesn't count, so a fresh `new AudioContext()` made only when the call
// arrives stays silently suspended on iOS (this is exactly why it worked
// on Android but not iOS). The fix: create ONE shared context up front,
// primed during an early real gesture elsewhere in the app (the
// login/register button), and reuse that SAME instance later -- iOS ties
// the "unlocked" state to the context instance, not the moment of playback.
let ctx = null;
let intervalId = null;

function getContext() {
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  return ctx;
}

// Call this from within a real click/tap handler, as early in the session
// as possible (e.g. the login button) -- safe to call repeatedly.
export function unlockAudio() {
  const c = getContext();
  c.resume?.().catch(() => {});
}

function beep(c, frequency, startTime, duration) {
  const osc = c.createOscillator();
  const gain = c.createGain();
  osc.type = 'sine';
  osc.frequency.value = frequency;
  gain.gain.setValueAtTime(0.0001, startTime);
  gain.gain.exponentialRampToValueAtTime(0.25, startTime + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
  osc.connect(gain);
  gain.connect(c.destination);
  osc.start(startTime);
  osc.stop(startTime + duration + 0.05);
}

export function startRingtone() {
  if (intervalId) return; // already ringing
  const c = getContext();
  c.resume?.().catch(() => {}); // in case it drifted back to suspended
  const ring = () => {
    const now = c.currentTime;
    beep(c, 950, now, 0.4);
    beep(c, 1400, now + 0.45, 0.4);
  };
  ring();
  intervalId = setInterval(ring, 2000);
}

export function stopRingtone() {
  if (intervalId) { clearInterval(intervalId); intervalId = null; }
}

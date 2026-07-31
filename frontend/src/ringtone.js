// Synthesized ring (no audio asset to source/ship) -- a plain two-tone
// beep pattern repeated on an interval, standard "ring-ring... pause"
// cadence. Module-level state (not a hook) since only one ringtone should
// ever play at a time regardless of which component triggers it.
let ctx = null;
let intervalId = null;

function beep(frequency, startTime, duration) {
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = 'sine';
  osc.frequency.value = frequency;
  gain.gain.setValueAtTime(0.0001, startTime);
  gain.gain.exponentialRampToValueAtTime(0.25, startTime + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(startTime);
  osc.stop(startTime + duration + 0.05);
}

export function startRingtone() {
  if (ctx) return; // already ringing
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  ctx.resume?.().catch(() => {});
  const ring = () => {
    if (!ctx) return;
    const now = ctx.currentTime;
    beep(950, now, 0.4);
    beep(1400, now + 0.45, 0.4);
  };
  ring();
  intervalId = setInterval(ring, 2000);
}

export function stopRingtone() {
  if (intervalId) { clearInterval(intervalId); intervalId = null; }
  if (ctx) { const c = ctx; ctx = null; c.close().catch(() => {}); }
}

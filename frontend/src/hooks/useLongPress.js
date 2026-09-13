import { useRef } from 'react';

const LONG_PRESS_MS = 450;
const MOVE_CANCEL_PX = 10; // a touch-scroll shouldn't also fire a long-press

/// Long-press (touch) / press-and-hold (mouse) gesture -- the web
/// equivalent of WhatsApp/Telegram's "hold a row/message for quick
/// actions." Call once per component (it's a hook), then call the
/// returned `bind(onLongPress)` freely per row/item inside a .map() --
/// `bind` itself is a plain function, not a hook, so this is safe to use
/// for a whole list without violating the rules of hooks. Only one press
/// can be in flight at a time (true by construction -- you have one
/// pointer), so sharing the timer ref across every bound item is safe.
export function useLongPress() {
  const timerRef = useRef(null);
  const startRef = useRef({ x: 0, y: 0 });

  const clear = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
  };

  return function bind(onLongPress) {
    const start = (x, y) => {
      startRef.current = { x, y };
      clear();
      timerRef.current = setTimeout(() => onLongPress(x, y), LONG_PRESS_MS);
    };
    const moveCancel = (x, y) => {
      if (!timerRef.current) return;
      const dx = x - startRef.current.x, dy = y - startRef.current.y;
      if (Math.hypot(dx, dy) > MOVE_CANCEL_PX) clear();
    };
    return {
      onMouseDown: (e) => start(e.clientX, e.clientY),
      onMouseMove: (e) => moveCancel(e.clientX, e.clientY),
      onMouseUp: clear,
      onMouseLeave: clear,
      onTouchStart: (e) => { const t = e.touches[0]; start(t.clientX, t.clientY); },
      onTouchMove: (e) => { const t = e.touches[0]; if (t) moveCancel(t.clientX, t.clientY); },
      onTouchEnd: clear,
      onContextMenu: (e) => {
        // Right-click does the same thing on desktop, instantly -- no
        // reason to make mouse users hold for 450ms when they already have
        // the platform-native gesture for "give me a menu here."
        e.preventDefault();
        clear();
        onLongPress(e.clientX, e.clientY);
      },
    };
  };
}

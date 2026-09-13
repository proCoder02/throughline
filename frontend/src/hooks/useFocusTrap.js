import { useEffect } from 'react';

const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';

/// Traps Tab/Shift+Tab inside a modal container while it's open, and
/// restores focus to whatever had it beforehand on close -- only for
/// content that actually blocks the rest of the page (the confirm dialog,
/// the call overlay). InfoPanel deliberately does NOT use this: it's a
/// non-blocking side panel, same as Discord's own member list, and the
/// chat behind it stays fully interactive while it's open.
export function useFocusTrap(active, containerRef, onEscape) {
  useEffect(() => {
    if (!active || !containerRef.current) return undefined;
    const container = containerRef.current;
    const previouslyFocused = document.activeElement;

    const focusables = () => Array.from(container.querySelectorAll(FOCUSABLE)).filter((el) => el.offsetParent !== null);
    focusables()[0]?.focus();

    const onKeyDown = (e) => {
      if (e.key === 'Escape' && onEscape) {
        onEscape();
        return;
      }
      if (e.key !== 'Tab') return;
      const items = focusables();
      if (!items.length) return;
      const idx = items.indexOf(document.activeElement);
      if (e.shiftKey && idx <= 0) {
        e.preventDefault();
        items[items.length - 1].focus();
      } else if (!e.shiftKey && idx === items.length - 1) {
        e.preventDefault();
        items[0].focus();
      }
    };

    container.addEventListener('keydown', onKeyDown);
    return () => {
      container.removeEventListener('keydown', onKeyDown);
      if (previouslyFocused && typeof previouslyFocused.focus === 'function') previouslyFocused.focus();
    };
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps
}

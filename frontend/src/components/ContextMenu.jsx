import { useEffect, useRef } from 'react';

/// Floating action menu opened by long-press/right-click (see
/// useLongPress) -- positioned at the press point, clamped so it never
/// renders off-screen. `items` is [{label, icon, onSelect, danger}].
export default function ContextMenu({ x, y, items, onClose }) {
  const ref = useRef(null);

  useEffect(() => {
    const onPointerDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
    const onKeyDown = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('touchstart', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('touchstart', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose]);

  // Clamp so a press near the right/bottom edge doesn't render the menu
  // partly off-screen -- 180px/ (items*36+16) roughly matches the CSS below.
  const width = 200;
  const height = items.length * 40 + 16;
  const left = Math.min(x, window.innerWidth - width - 8);
  const top = Math.min(y, window.innerHeight - height - 8);

  return (
    <div className="context-menu" role="menu" ref={ref} style={{ left, top }}>
      {items.map((item, i) => (
        <button
          key={i} role="menuitem" className={'context-menu-item' + (item.danger ? ' danger' : '')}
          onClick={() => { item.onSelect(); onClose(); }}
        >
          {item.icon}
          <span>{item.label}</span>
        </button>
      ))}
    </div>
  );
}

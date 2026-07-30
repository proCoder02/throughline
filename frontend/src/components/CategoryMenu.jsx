import { useEffect, useRef, useState } from 'react';
import { MoreIcon, CheckIcon } from '../icons.jsx';

const OPTIONS = [
  { key: 'all', label: 'All categories' },
  { key: 'personal', label: 'Personal' },
  { key: 'office', label: 'Office' },
  { key: 'study', label: 'Study' },
];

export default function CategoryMenu({ value, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onClickAway = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onClickAway);
    return () => document.removeEventListener('mousedown', onClickAway);
  }, [open]);

  return (
    <div className="category-menu" ref={ref}>
      <button title="Filter chats" onClick={() => setOpen((v) => !v)}><MoreIcon /></button>
      {open && (
        <div className="category-menu-dropdown">
          {OPTIONS.map((opt) => (
            <div
              key={opt.key}
              className="category-menu-item"
              onClick={() => { onChange(opt.key); setOpen(false); }}
            >
              <span>{opt.label}</span>
              {value === opt.key && <CheckIcon />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

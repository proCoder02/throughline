import { useEffect, useRef, useState } from 'react';
import { MoreIcon, CheckIcon } from '../icons.jsx';
import { apiJson } from '../api.js';

const cap = (s) => s[0].toUpperCase() + s.slice(1);

export default function CategoryMenu({ value, onChange }) {
  const [open, setOpen] = useState(false);
  // Personal/office/study are always available; custom ones are per-user
  // (Settings -> Your categories), fetched here so a newly added one shows
  // up in this filter without any other code needing to know it exists.
  const [options, setOptions] = useState([{ key: 'all', label: 'All categories' }]);
  const ref = useRef(null);

  useEffect(() => {
    apiJson('/categories')
      .then((d) => setOptions([
        { key: 'all', label: 'All categories' },
        ...d.builtin.map((c) => ({ key: c, label: cap(c) })),
        ...d.custom.map((c) => ({ key: c, label: c })),
      ]))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!open) return;
    const onClickAway = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onClickAway);
    return () => document.removeEventListener('mousedown', onClickAway);
  }, [open]);

  return (
    <div className="category-menu" ref={ref}>
      <button
        title="Filter chats" aria-label="Filter by category" aria-haspopup="true" aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <MoreIcon />
      </button>
      {open && (
        <div className="category-menu-dropdown" role="menu">
          {options.map((opt) => (
            <button
              key={opt.key}
              className="category-menu-item" role="menuitemradio" aria-checked={value === opt.key}
              onClick={() => { onChange(opt.key); setOpen(false); }}
            >
              <span>{opt.label}</span>
              {value === opt.key && <CheckIcon aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

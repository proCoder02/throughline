import { CloseIcon } from '../icons.jsx';

/// Discord-style slide-in side panel -- conversation/friend info, toggled by
/// the info button in a chat header. Always mounted (so the slide-out
/// transition plays on close too), visibility is purely the `open` class.
export default function InfoPanel({ open, onClose, title, children }) {
  return (
    <div className={'info-panel' + (open ? ' open' : '')} aria-hidden={!open} role="complementary" aria-label={title}>
      <div className="info-panel-header">
        <span className="info-panel-title">{title}</span>
        <button className="conv-link-btn" title="Close" aria-label="Close" onClick={onClose}><CloseIcon /></button>
      </div>
      <div className="info-panel-body">{children}</div>
    </div>
  );
}

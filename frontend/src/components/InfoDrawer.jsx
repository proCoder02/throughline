import { BackIcon } from '../icons.jsx';

export default function InfoDrawer({ title, text, onClose }) {
  return (
    <div className="info-drawer">
      <div className="info-drawer-header">
        <button onClick={onClose}><BackIcon /></button>
        <strong>{title}</strong>
      </div>
      <div className="info-drawer-body">{text || '(no transcript)'}</div>
    </div>
  );
}

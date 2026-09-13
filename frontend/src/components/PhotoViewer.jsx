import { useRef } from 'react';
import { useFocusTrap } from '../hooks/useFocusTrap.js';
import { CloseIcon } from '../icons.jsx';

/// WhatsApp-style full-screen profile-picture viewer -- opened by clicking
/// a friend's avatar wherever it appears large enough to matter (chat
/// header, Friend info panel, friend detail view). Renders nothing when
/// there's no real photo (an initials-only avatar has nothing to view).
export default function PhotoViewer({ url, name, onClose }) {
  const cardRef = useRef(null);
  useFocusTrap(true, cardRef, onClose);
  if (!url) return null;

  return (
    <div
      className="photo-viewer-overlay" role="dialog" aria-modal="true"
      aria-label={name ? `${name}'s profile picture` : 'Profile picture'}
      onClick={onClose}
    >
      <div className="photo-viewer-card" ref={cardRef} onClick={(e) => e.stopPropagation()}>
        <button className="photo-viewer-close" title="Close" aria-label="Close" onClick={onClose} autoFocus>
          <CloseIcon />
        </button>
        <img src={url} alt="" className="photo-viewer-image" />
      </div>
    </div>
  );
}

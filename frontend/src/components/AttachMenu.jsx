import { useEffect } from 'react';
import { PhotoIcon, PdfIcon } from '../icons.jsx';

// Web counterpart of the Flutter client's _showAttachMenu (global_chat_body.dart)
// -- same bottom-sheet-with-two-tiles layout and choice ('Photo' / 'PDF
// document'), not a plain dropdown. This also sidesteps a real iOS Safari
// issue: a single <input type="file"> whose accept mixes image/* with a
// document type is unreliable there (sometimes only offers Photo Library,
// sometimes drops the Files/Browse option entirely) -- two tiles, each
// wired to its OWN single-type file input, is both the same UX as the app
// and the more robust choice on iOS.
export default function AttachMenu({ onPickPhoto, onPickPdf, onClose }) {
  useEffect(() => {
    const onKeyDown = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div className="attach-menu-overlay" onClick={onClose}>
      <div className="attach-menu-sheet" role="menu" onClick={(e) => e.stopPropagation()}>
        <button
          type="button" role="menuitem" className="attach-tile"
          onClick={() => { onClose(); onPickPhoto(); }}
        >
          <span className="attach-tile-icon" style={{ background: 'color-mix(in srgb, var(--wa-accent) 15%, transparent)', color: 'var(--wa-accent)' }}>
            <PhotoIcon />
          </span>
          <span className="attach-tile-label">Photo</span>
        </button>
        <button
          type="button" role="menuitem" className="attach-tile"
          onClick={() => { onClose(); onPickPdf(); }}
        >
          <span className="attach-tile-icon" style={{ background: 'color-mix(in srgb, #ef4444 15%, transparent)', color: '#ef4444' }}>
            <PdfIcon />
          </span>
          <span className="attach-tile-label">PDF document</span>
        </button>
      </div>
    </div>
  );
}

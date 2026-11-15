import { useRef, useState } from 'react';
import { AttachIcon, SendIcon } from '../icons.jsx';
import AttachMenu from './AttachMenu.jsx';

// Matches theme.css's single responsive breakpoint (the whole app swaps to
// the phone layout -- bottom tab bar, single-pane nav, etc. -- below this
// width), so the attach menu's phone-app-style bottom sheet only appears
// where the rest of the UI already looks like the phone app. Desktop web
// keeps the plain "click to browse files" behavior it always had.
const MOBILE_BREAKPOINT_PX = 860;
const isPhoneViewport = () => window.innerWidth < MOBILE_BREAKPOINT_PX;

export default function Composer({
  onSend, disabled, placeholder, extraButton, onTyping, onImageSelected, onPdfSelected,
  attachAccept = 'image/*', attachLabel = 'Attach an image',
}) {
  const [value, setValue] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const fileInputRef = useRef(null);
  const photoInputRef = useRef(null);
  const pdfInputRef = useRef(null);

  const send = () => {
    const text = value.trim();
    if (!text) return;
    setValue('');
    onSend(text);
  };

  // Desktop fallback for the combined photo+PDF case -- a plain OS file
  // browser dialog doesn't have the narrowed-picker problem iOS Safari's
  // mixed-accept input has, so there's no need for the two-tile menu there.
  const handleCombinedFileChange = (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (file.type === 'application/pdf') onPdfSelected(file);
    else onImageSelected(file);
  };
  const handleFileChange = (e) => {
    const file = e.target.files[0];
    e.target.value = ''; // allow picking the same file again later
    if (file) onImageSelected(file);
  };
  const handlePhotoChange = (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (file) onImageSelected(file);
  };
  const handlePdfChange = (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (file) onPdfSelected(file);
  };

  return (
    <div className="composer">
      <textarea
        rows={1}
        placeholder={placeholder || 'Type a message'}
        aria-label={placeholder || 'Type a message'}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          if (e.target.value.trim()) onTyping?.();
        }}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
      />
      {onImageSelected && onPdfSelected ? (
        // Phone: two single-type pickers behind a Photo/PDF menu, matching
        // the Flutter client's attach sheet exactly -- also sidesteps a real
        // iOS Safari issue where one <input> mixing image/* with a document
        // type behaves unreliably. Desktop: the plain combined-accept input
        // it always had, no app-style bottom sheet.
        <>
          <input ref={photoInputRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handlePhotoChange} />
          <input ref={pdfInputRef} type="file" accept="application/pdf" style={{ display: 'none' }} onChange={handlePdfChange} />
          <input ref={fileInputRef} type="file" accept="image/*,application/pdf" style={{ display: 'none' }} onChange={handleCombinedFileChange} />
          <button
            type="button"
            className="send-btn"
            title="Attach a photo or PDF"
            aria-label="Attach a photo or PDF"
            aria-haspopup="true"
            onClick={() => { if (isPhoneViewport()) setMenuOpen(true); else fileInputRef.current.click(); }}
          >
            <AttachIcon />
          </button>
          {menuOpen && (
            <AttachMenu
              onClose={() => setMenuOpen(false)}
              onPickPhoto={() => photoInputRef.current.click()}
              onPickPdf={() => pdfInputRef.current.click()}
            />
          )}
        </>
      ) : onImageSelected && (
        <>
          <input
            ref={fileInputRef}
            type="file"
            accept={attachAccept}
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          <button
            type="button"
            className="send-btn"
            title={attachLabel}
            aria-label={attachLabel}
            onClick={() => fileInputRef.current.click()}
          >
            <AttachIcon />
          </button>
        </>
      )}
      {extraButton}
      <button className="send-btn" disabled={disabled} onClick={send} aria-label="Send">
        <SendIcon />
      </button>
    </div>
  );
}

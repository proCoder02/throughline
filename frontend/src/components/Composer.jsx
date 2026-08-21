import { useRef, useState } from 'react';
import { AttachIcon, SendIcon } from '../icons.jsx';

export default function Composer({ onSend, disabled, placeholder, extraButton, onTyping, onImageSelected }) {
  const [value, setValue] = useState('');
  const fileInputRef = useRef(null);

  const send = () => {
    const text = value.trim();
    if (!text) return;
    setValue('');
    onSend(text);
  };

  const handleFileChange = (e) => {
    const file = e.target.files[0];
    e.target.value = ''; // allow picking the same file again later
    if (!file) return;
    onImageSelected(file);
  };

  return (
    <div className="composer">
      <textarea
        rows={1}
        placeholder={placeholder || 'Type a message'}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          if (e.target.value.trim()) onTyping?.();
        }}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
      />
      {onImageSelected && (
        <>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          <button
            type="button"
            className="send-btn"
            title="Attach an image"
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

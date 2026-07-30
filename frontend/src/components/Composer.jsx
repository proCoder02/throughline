import { useState } from 'react';
import { SendIcon } from '../icons.jsx';

export default function Composer({ onSend, disabled, placeholder, extraButton }) {
  const [value, setValue] = useState('');

  const send = () => {
    const text = value.trim();
    if (!text) return;
    setValue('');
    onSend(text);
  };

  return (
    <div className="composer">
      <textarea
        rows={1}
        placeholder={placeholder || 'Type a message'}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
      />
      {extraButton}
      <button className="send-btn" disabled={disabled} onClick={send} aria-label="Send">
        <SendIcon />
      </button>
    </div>
  );
}

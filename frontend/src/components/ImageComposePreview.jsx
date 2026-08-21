import { useEffect, useMemo, useState } from 'react';
import { CloseIcon, SendIcon } from '../icons.jsx';

// Shown in place of the normal Composer once an image has been picked --
// WhatsApp-style: you see the image and add a description before it
// actually sends, rather than it going out the instant you pick it.
export default function ImageComposePreview({ file, sending, onSend, onCancel }) {
  const [description, setDescription] = useState('');
  const previewUrl = useMemo(() => URL.createObjectURL(file), [file]);

  useEffect(() => () => URL.revokeObjectURL(previewUrl), [previewUrl]);

  const canSend = description.trim().length > 0 && !sending;

  return (
    <div className="composer image-compose-preview">
      <div className="image-compose-row">
        <img src={previewUrl} alt="" className="image-compose-thumb" />
        <textarea
          rows={1}
          autoFocus
          placeholder="Add a description..."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              if (canSend) onSend(description.trim());
            }
          }}
        />
        <button type="button" className="send-btn" title="Cancel" onClick={onCancel}>
          <CloseIcon />
        </button>
        <button
          type="button"
          className="send-btn"
          disabled={!canSend}
          title="Send"
          onClick={() => onSend(description.trim())}
        >
          <SendIcon />
        </button>
      </div>
    </div>
  );
}

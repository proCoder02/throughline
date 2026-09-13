import { useEffect, useMemo, useState } from 'react';
import { CloseIcon, SendIcon, FileIcon, PlayIcon } from '../icons.jsx';

/// Shown in place of the normal Composer once a file has been picked for a
/// DM attachment -- WhatsApp-style preview-before-send, but the caption is
/// optional here (unlike ImageComposePreview's vision-extraction flow,
/// where a description is required to actually do anything useful).
export default function AttachmentComposePreview({ file, kind, uploading, progress, onSend, onCancel }) {
  const [caption, setCaption] = useState('');
  const previewUrl = useMemo(() => (kind === 'image' ? URL.createObjectURL(file) : null), [file, kind]);
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  const send = () => { if (!uploading) onSend(caption.trim()); };

  return (
    <div className="composer">
      <div className="image-compose-row">
        {kind === 'image' ? (
          <img src={previewUrl} alt="" className="image-compose-thumb" />
        ) : (
          <div className="image-compose-thumb file-thumb">{kind === 'video' ? <PlayIcon /> : <FileIcon />}</div>
        )}
        <textarea
          rows={1}
          autoFocus
          placeholder="Add a caption (optional)"
          aria-label="Caption"
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
        />
        <button type="button" className="send-btn" title="Cancel" aria-label="Cancel" disabled={uploading} onClick={onCancel}>
          <CloseIcon />
        </button>
        <button type="button" className="send-btn" title="Send" aria-label="Send" disabled={uploading} onClick={send}>
          <SendIcon />
        </button>
      </div>
      {uploading && (
        <div className="action-card-progress" style={{ margin: '0 16px 10px' }}>
          <div className="action-card-progress-fill" style={{ width: `${Math.round((progress || 0) * 100)}%` }} />
        </div>
      )}
    </div>
  );
}

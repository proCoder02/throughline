import { useState } from 'react';
import FormattedText from './FormattedText.jsx';
import ActionCard from './ActionCard.jsx';
import ContextMenu from './ContextMenu.jsx';
import { useLongPress } from '../hooks/useLongPress.js';
import { showToast } from '../lib/notify.js';
import { CopyIcon, FileIcon, DownloadIcon, BrainIcon } from '../icons.jsx';

function formatTime(value) {
  if (!value) return '';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/// Renders an R2-backed attachment (see MEDIA_STORAGE_PLAN.md) by MIME
/// type. `thumbnail` (the inline base64 data: URL) shows immediately and
/// stays as the fallback if the full-resolution `url` ever 404s -- e.g.
/// once an R2 Object Lifecycle Rule expires it -- so an old message
/// degrades to "small preview" rather than a broken-image icon.
function Attachment({ url, type, thumbnail, summary }) {
  const [broken, setBroken] = useState(false);
  if (!url) return null;

  if (type?.startsWith('image/')) {
    if (broken) {
      return thumbnail ? (
        <div>
          <img src={thumbnail} alt="" className="bubble-image" style={{ opacity: 0.6 }} />
          <div className="hint" style={{ margin: '4px 0 0' }}>Photo no longer available</div>
        </div>
      ) : <div className="hint">Photo no longer available</div>;
    }
    return <img src={url} alt="" className="bubble-image" onError={() => setBroken(true)} />;
  }

  if (type?.startsWith('video/')) {
    return broken ? (
      <div className="hint">Video no longer available</div>
    ) : (
      <video controls poster={thumbnail || undefined} className="bubble-image" style={{ width: '100%' }} onError={() => setBroken(true)}>
        <source src={url} type={type} />
      </video>
    );
  }

  const filename = decodeURIComponent(url.split('/').pop() || 'file').replace(/^[0-9a-f]{32}\./, '');
  return (
    <div>
      <a href={url} target="_blank" rel="noreferrer" className="attachment-file-card">
        <FileIcon />
        <span className="attachment-file-name">{filename}</span>
        <DownloadIcon />
      </a>
      {/* Cognitive Sharing add-on: a 20-30 word AI summary of the document's
          content, only ever present when both people in the DM have
          sharing turned on (see app.py's _generate_attachment_summary). */}
      {summary && <div className="attachment-summary"><BrainIcon />{summary}</div>}
    </div>
  );
}

/// WhatsApp-style chat bubble -- right-aligned + accent-tinted for `mine`,
/// left-aligned + panel-colored otherwise. Side + color alone tell you who
/// said what (no per-message name label needed), used identically for the
/// AI Q&A thread (You vs Assistant) and 1:1 DMs (you vs your friend).
export default function MessageRow({
  mine, timestamp, content, formatted = false, imageUrl,
  attachmentUrl, attachmentType, thumbnailDataUrl, attachmentSummary,
  actionCard, onActionResolved, trailingIcon,
}) {
  const time = formatTime(timestamp);
  const [menu, setMenu] = useState(null);
  const bindLongPress = useLongPress();

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(content || '');
      showToast('Copied');
    } catch {
      showToast('Could not copy -- your browser blocked clipboard access.', 'error');
    }
  };

  return (
    <div className={'bubble-row' + (mine ? ' out' : ' in')}>
      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y} onClose={() => setMenu(null)}
          items={[{ label: 'Copy text', icon: <CopyIcon />, onSelect: copyText }]}
        />
      )}
      <div
        className={'bubble' + (mine ? ' out' : ' in')}
        {...(content ? bindLongPress((x, y) => setMenu({ x, y })) : {})}
      >
        {imageUrl && <img src={imageUrl} alt="" className="bubble-image" />}
        {attachmentUrl && (
          <Attachment url={attachmentUrl} type={attachmentType} thumbnail={thumbnailDataUrl} summary={attachmentSummary} />
        )}
        {formatted ? <FormattedText text={content} /> : content}
        {(time || trailingIcon) && (
          <span className="bubble-time">
            {time}
            {trailingIcon}
          </span>
        )}
        {actionCard && <ActionCard card={actionCard} onResolved={onActionResolved} />}
      </div>
    </div>
  );
}

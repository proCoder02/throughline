import { useEffect, useRef, useState } from 'react';
import MessageRow from './MessageRow.jsx';
import Composer from './Composer.jsx';
import ImageComposePreview from './ImageComposePreview.jsx';
import InfoPanel from './InfoPanel.jsx';
import { BackIcon, TrashIcon, MicIcon, CloseIcon, InfoIcon } from '../icons.jsx';

export default function ChatThread({
  title, isLive, liveStatus, seenIndices = [], speakerNames,
  pendingSpeakerIndex = null, knownSpeakers, onNameSpeaker, onSkipSpeaker, onReopenPrompt,
  tags, onTagClick, onDismissTag,
  messages, onSend, sending, onDelete, onListen, onStopListen, onBack,
  subtitle, placeholder, emptyHint, onSendImage, onActionResolved,
}) {
  const scrollRef = useRef(null);
  // Only relevant when onSendImage is provided (global chat today) -- a
  // picked-but-not-yet-sent image, held here rather than in the parent so
  // the parent only has to care about the final send, same shape as how
  // Composer already owns its own in-progress text without the parent
  // knowing about every keystroke.
  const [pendingImage, setPendingImage] = useState(null);
  const [infoOpen, setInfoOpen] = useState(false);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length]);

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <button className="back-btn" title="Back" aria-label="Back to chat list" onClick={onBack}><BackIcon /></button>
        <span className="avatar">{(title || '?')[0]}</span>
        <div className="chat-header-info">
          <div className="chat-header-name">{title}</div>
          <div className="chat-header-sub">
            {isLive ? <span className="live-badge"><span className="dot" />{liveStatus || 'Listening'}</span> : (subtitle || 'Ask about this conversation')}
          </div>
        </div>
        <div className="chat-header-actions">
          <button
            title="Conversation info" aria-label="Conversation info" aria-pressed={infoOpen}
            className={infoOpen ? 'active' : ''} onClick={() => setInfoOpen((v) => !v)}
          >
            <InfoIcon />
          </button>
          {onDelete && <button title="Delete conversation" aria-label="Delete conversation" onClick={onDelete}><TrashIcon /></button>}
        </div>
      </div>

      {isLive && seenIndices.length > 0 && (
        <div className="speaker-chips">
          {seenIndices.map((idx) => (
            <div className="speaker-chip" key={idx}>
              <span>{speakerNames[idx] || 'Speaker ' + idx}</span>
              <button onClick={() => onReopenPrompt(idx)}>Change</button>
            </div>
          ))}
        </div>
      )}

      {pendingSpeakerIndex !== null && (
        <SpeakerPrompt
          index={pendingSpeakerIndex}
          known={knownSpeakers}
          onSave={(name) => onNameSpeaker(pendingSpeakerIndex, name)}
          onSkip={onSkipSpeaker}
        />
      )}

      <div className="messages" ref={scrollRef}>
        {messages.map((m, i) => (
          <MessageRow
            key={i}
            mine={m.role === 'user'}
            content={m.content}
            formatted={m.role !== 'user'}
            imageUrl={m.imageUrl}
            actionCard={m.actionCard}
            onActionResolved={onActionResolved}
          />
        ))}
        {!messages.length && (
          <div className="list-empty">{emptyHint || 'Ask a question about this conversation to get started.'}</div>
        )}
      </div>

      {tags?.length > 0 && (
        <div className="topic-tags">
          {tags.map((tag) => (
            <div key={tag} className="topic-tag">
              <button className="topic-tag-label" title={`Ask about "${tag}"`} aria-label={`Ask about ${tag}`} onClick={() => onTagClick?.(tag)}>
                {tag}
              </button>
              <button className="topic-tag-x" title="Dismiss" aria-label={`Dismiss "${tag}"`} onClick={() => onDismissTag?.(tag)}>
                <CloseIcon />
              </button>
            </div>
          ))}
        </div>
      )}

      {pendingImage ? (
        <ImageComposePreview
          file={pendingImage}
          sending={sending}
          onCancel={() => setPendingImage(null)}
          onSend={(description) => {
            onSendImage(pendingImage, description);
            setPendingImage(null);
          }}
        />
      ) : (
        <Composer
          onSend={onSend}
          disabled={sending}
          placeholder={placeholder || 'Ask about this conversation...'}
          onImageSelected={onSendImage ? setPendingImage : undefined}
          extraButton={onListen && (
            <button
              className="send-btn"
              style={isLive ? { background: 'var(--wa-danger)' } : undefined}
              title={isLive ? 'Stop listening' : 'Resume listening on this conversation'}
              aria-label={isLive ? 'Stop listening' : 'Resume listening on this conversation'}
              onClick={isLive ? onStopListen : onListen}
            >
              <MicIcon />
            </button>
          )}
        />
      )}

      <InfoPanel open={infoOpen} onClose={() => setInfoOpen(false)} title="Conversation info">
        <div className="detail-title" style={{ fontSize: 15 }}>{title}</div>
        {subtitle && <div className="hint" style={{ marginTop: 4 }}>{subtitle}</div>}
        {tags?.length > 0 && (
          <div className="profile-section" style={{ borderTop: 'none', marginTop: 16, paddingTop: 0 }}>
            <div className="profile-section-title">Topics</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {tags.map((tag) => <span key={tag} className="topic-tag" style={{ cursor: 'default' }}>{tag}</span>)}
            </div>
          </div>
        )}
      </InfoPanel>
    </div>
  );
}

function SpeakerPrompt({ index, known, onSave, onSkip }) {
  const [name, setName] = useState('');
  return (
    <div className="speaker-prompt">
      <strong>Speaker {index}</strong> — who is this?
      <div className="row2">
        <select onChange={(e) => e.target.value && onSave(e.target.value)} defaultValue="">
          <option value="" disabled>Pick existing speaker...</option>
          {known.map((s) => <option key={s.id} value={s.name}>{s.name}</option>)}
        </select>
        <input placeholder="Or type a new name" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="primary" onClick={() => name.trim() && onSave(name.trim())}>Save</button>
        <button onClick={onSkip}>Skip</button>
      </div>
    </div>
  );
}

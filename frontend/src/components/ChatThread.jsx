import { useEffect, useRef, useState } from 'react';
import MessageBubble from './MessageBubble.jsx';
import Composer from './Composer.jsx';
import { InfoIcon, TrashIcon, MicIcon } from '../icons.jsx';

export default function ChatThread({
  title, isLive, liveStatus, seenIndices, speakerNames,
  pendingSpeakerIndex, knownSpeakers, onNameSpeaker, onSkipSpeaker, onReopenPrompt,
  messages, onSend, sending, onToggleInfo, onDelete, onListen, onStopListen,
}) {
  const scrollRef = useRef(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length]);

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <span className="avatar">{(title || '?')[0]}</span>
        <div className="chat-header-info">
          <div className="chat-header-name">{title}</div>
          <div className="chat-header-sub">
            {isLive ? <span className="live-badge"><span className="dot" />{liveStatus || 'Listening'}</span> : 'Ask about this conversation'}
          </div>
        </div>
        <div className="chat-header-actions">
          <button title="Transcript" onClick={onToggleInfo}><InfoIcon /></button>
          {onDelete && <button title="Delete conversation" onClick={onDelete}><TrashIcon /></button>}
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
          <MessageBubble key={i} role={m.role} content={m.content} />
        ))}
        {!messages.length && (
          <div className="list-empty">Ask a question about this conversation to get started.</div>
        )}
      </div>

      <Composer
        onSend={onSend}
        disabled={sending}
        placeholder="Ask about this conversation..."
        extraButton={onListen && (
          <button
            className="send-btn"
            style={isLive ? { background: '#EA0038' } : undefined}
            title={isLive ? 'Stop listening' : 'Resume listening on this conversation'}
            onClick={isLive ? onStopListen : onListen}
          >
            <MicIcon />
          </button>
        )}
      />
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

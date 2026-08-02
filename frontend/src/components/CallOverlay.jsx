import { MicIcon, PhoneIcon } from '../icons.jsx';

// Global overlay -- rendered at the App level (not inside any tab) so an
// incoming call or an in-progress call stays visible no matter which
// section the user is looking at.
export default function CallOverlay({ incomingCall, activeCall, onAccept, onDecline, onLeave, onToggleMute, onEnableAudio }) {
  if (activeCall) {
    const count = activeCall.participants.length + 1;
    const names = activeCall.participants.map((p) => p.name).join(', ');
    return (
      <div className="call-overlay">
        <div className="call-card">
          <span className="avatar lg">{(activeCall.roomName || '?')[0].toUpperCase()}</span>
          <div className="call-title">Call in progress</div>
          <div className="call-sub">
            {count} participant{count === 1 ? '' : 's'}{names && ` — ${names}`}
          </div>
          {activeCall.droppedNotices?.map((n) => (
            <div key={n.id} className="call-drop-notice">{n.name} dropped from the call</div>
          ))}
          <div className="call-recording-notice">This call is being recorded</div>
          {activeCall.audioBlocked && (
            <button className="btn call-audio-blocked" onClick={onEnableAudio}>
              Tap to enable audio
            </button>
          )}
          <div className="call-actions">
            <button className="btn secondary" onClick={onToggleMute}>{activeCall.muted ? 'Unmute' : 'Mute'}</button>
            <button className="btn danger" onClick={onLeave}>
              <PhoneIcon /> Leave
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (incomingCall) {
    return (
      <div className="call-overlay">
        <div className="call-card">
          <span className="avatar lg">{(incomingCall.callerName || '?')[0].toUpperCase()}</span>
          <div className="call-title">{incomingCall.callerName}</div>
          <div className="call-sub">Incoming call...</div>
          <div className="call-recording-notice">This call is being recorded</div>
          <div className="call-actions">
            <button className="btn danger" onClick={onDecline}>Decline</button>
            <button className="btn" onClick={onAccept}>
              <MicIcon /> Accept
            </button>
          </div>
        </div>
      </div>
    );
  }

  return null;
}

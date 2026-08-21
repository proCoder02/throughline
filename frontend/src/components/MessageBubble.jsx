import FormattedText from './FormattedText.jsx';

export default function MessageBubble({ role, content, time, imageUrl }) {
  const out = role === 'user';
  return (
    <div className={'bubble-row ' + (out ? 'out' : 'in')}>
      <div className={'bubble ' + (out ? 'out' : 'in')}>
        {/* Session-only -- the backend never persists raw image bytes, so
            this only ever has a value for a message sent earlier in the
            current session, never for one loaded from history/reload. */}
        {imageUrl && <img src={imageUrl} alt="" className="bubble-image" />}
        {out ? content : <FormattedText text={content} />}
        {time && <span className="bubble-time">{time}</span>}
      </div>
    </div>
  );
}

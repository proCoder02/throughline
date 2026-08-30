import FormattedText from './FormattedText.jsx';
import ActionCard from './ActionCard.jsx';

export default function MessageBubble({ role, content, time, imageUrl, actionCard, onActionResolved }) {
  const out = role === 'user';
  return (
    <div className={'bubble-row ' + (out ? 'out' : 'in')}>
      <div className={'bubble ' + (out ? 'out' : 'in')}>
        {/* Session-only -- the backend never persists raw image bytes, so
            this only ever has a value for a message sent earlier in the
            current session, never for one loaded from history/reload. */}
        {imageUrl && <img src={imageUrl} alt="" className="bubble-image" />}
        {out ? content : <FormattedText text={content} />}
        {/* Cognitive Commerce (Swiggy MCP) -- only present on an assistant
            reply that actually found real, orderable results; see
            action_card in the /chat/global response. */}
        {actionCard && <ActionCard card={actionCard} onResolved={onActionResolved} />}
        {time && <span className="bubble-time">{time}</span>}
      </div>
    </div>
  );
}

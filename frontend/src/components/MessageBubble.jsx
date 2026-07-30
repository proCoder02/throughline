export default function MessageBubble({ role, content, time }) {
  const out = role === 'user';
  return (
    <div className={'bubble-row ' + (out ? 'out' : 'in')}>
      <div className={'bubble ' + (out ? 'out' : 'in')}>
        {content}
        {time && <span className="bubble-time">{time}</span>}
      </div>
    </div>
  );
}

import { useState } from 'react';
import { FoodOrderIcon } from '../icons.jsx';
import { post } from '../api.js';

// Cognitive Commerce (Swiggy MCP): renders the "would you like me to order
// this?" card the backend attaches to a chat reply as action_card. Purely
// presentational + its own tiny confirm/dismiss network calls -- the parent
// (ChatsSection) only needs to know the outcome to append a follow-up
// message. Never calls anything on mount: nothing happens here until the
// user taps a button, matching the "recommend -> ask -> act" rule this
// feature is built around.
export default function ActionCard({ card, onResolved }) {
  const [state, setState] = useState('pending'); // 'pending' | 'busy' | 'done'
  const [result, setResult] = useState(null);

  if (state === 'done') {
    return (
      <div className="action-card action-card-done">
        <FoodOrderIcon />
        <span>{result}</span>
      </div>
    );
  }

  const confirm = async () => {
    setState('busy');
    try {
      const data = await post('/commerce/swiggy/confirm', { action_id: card.id });
      setResult(`Order placed${data.external_order_id ? ` (#${data.external_order_id})` : ''}.`);
    } catch (e) {
      setResult(e.message || 'Order could not be placed.');
    } finally {
      setState('done');
      onResolved?.();
    }
  };

  const dismiss = async () => {
    setState('busy');
    try {
      await post('/commerce/swiggy/dismiss', { action_id: card.id });
    } catch (e) {
      // dismiss failing silently is fine -- worst case the suggestion just
      // stays shown; it can't accidentally place an order either way
    } finally {
      setResult('Okay, not ordering.');
      setState('done');
      onResolved?.();
    }
  };

  return (
    <div className="action-card">
      <div className="action-card-need">{card.need}</div>
      <ul className="action-card-items">
        {card.items.map((item, i) => <li key={i}>{item.label}</li>)}
      </ul>
      <div className="action-card-buttons">
        <button className="btn" disabled={state === 'busy'} onClick={confirm}>
          <FoodOrderIcon /> Order this
        </button>
        <button className="btn secondary" disabled={state === 'busy'} onClick={dismiss}>
          Not hungry
        </button>
      </div>
    </div>
  );
}

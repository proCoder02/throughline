import { useEffect, useRef, useState } from 'react';
import { FoodOrderIcon } from '../icons.jsx';
import { apiJson, post } from '../api.js';

const PAYMENT_POLL_INTERVAL_MS = 10000; // matches Swiggy's own docs' polling guidance (no faster than every 10s)
const DEFAULT_TRACK_POLL_INTERVAL_MS = 30000; // fallback when Swiggy's own pollingDuration hint is missing/unparseable
const MAX_TRACK_POLLS = 60; // ~30min+ at the default interval -- stop background polling eventually even if the order never reaches a state we recognize as final

// Cognitive Commerce (Swiggy MCP): renders the "would you like me to order
// this?" card the backend attaches to a chat reply as action_card. Purely
// presentational + its own tiny confirm/dismiss/poll network calls -- the
// parent (ChatsSection) only needs to know the outcome to append a
// follow-up message. Never calls anything on mount: nothing happens here
// until the user taps a button, matching the "recommend -> ask -> act"
// rule this feature is built around.
export default function ActionCard({ card, onResolved }) {
  // 'where is my order'-style messages attach a card already in tracking
  // mode (see swiggy_adapter.py's build_tracking_context) -- it skips the
  // usual pending/confirm flow entirely, since there's nothing to confirm.
  const isTrackingCard = card.mode === 'tracking';
  const [state, setState] = useState(isTrackingCard ? 'tracking' : 'pending'); // 'pending' | 'busy' | 'awaiting_payment' | 'tracking' | 'done'
  const [result, setResult] = useState(null);
  const [payment, setPayment] = useState(null); // { payment_link }
  const [tracking, setTracking] = useState(isTrackingCard ? card : null); // { title, subtitle, eta_text, progress_percentage, external_order_id }
  const pollRef = useRef(null);
  const trackPollCountRef = useRef(0);

  useEffect(() => {
    if (isTrackingCard) trackOrder(card.id, card.external_order_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Only Food items carry their own menu_item_id (Instamart/Dineout's
  // fallback path -- see swiggy_adapter.py's SEARCH-side note -- doesn't),
  // so radio selection only appears when there's actually something to
  // choose between; that path keeps the original single-button behavior,
  // letting the backend default to the first/top item as before.
  const selectable = card.items.some((item) => item.menu_item_id);
  const [selected, setSelected] = useState(card.items.find((item) => item.menu_item_id)?.menu_item_id ?? null);

  useEffect(() => () => clearInterval(pollRef.current), []);

  const finish = (text) => {
    clearInterval(pollRef.current);
    setResult(text);
    setState('done');
    onResolved?.();
  };

  // Real orderStatus values aren't fully enumerated in Swiggy's own docs,
  // so there's no reliable "stop polling" signal from status alone --
  // capped by MAX_TRACK_POLLS instead. Parses "30s"-style hints from
  // Swiggy's own response (pollingDuration) when present.
  const trackOrder = (actionId, externalOrderId) => {
    trackPollCountRef.current = 0;
    const poll = async () => {
      if (trackPollCountRef.current >= MAX_TRACK_POLLS) {
        clearInterval(pollRef.current);
        return;
      }
      trackPollCountRef.current += 1;
      try {
        const data = await apiJson(`/commerce/swiggy/track-order?action_id=${actionId}`);
        setTracking({ ...data, external_order_id: externalOrderId });
        const nextMs = parseFloat(data.polling_duration) * 1000 || DEFAULT_TRACK_POLL_INTERVAL_MS;
        clearInterval(pollRef.current);
        pollRef.current = setInterval(poll, nextMs);
      } catch (e) {
        clearInterval(pollRef.current);
      }
    };
    setState('tracking');
    poll();
  };

  const pollPayment = (paymentActionId) => {
    pollRef.current = setInterval(async () => {
      try {
        const data = await apiJson(`/commerce/swiggy/payment-status?payment_action_id=${paymentActionId}`);
        if (data.status === 'order_placed') {
          clearInterval(pollRef.current);
          onResolved?.();
          trackOrder(data.action_id, data.external_order_id);
        } else if (data.status === 'order_failed') {
          finish('Payment did not go through -- order was not placed.');
        }
        // 'awaiting_payment' -- keep polling, no state change
      } catch (e) {
        finish(e.message || 'Could not check payment status.');
      }
    }, PAYMENT_POLL_INTERVAL_MS);
  };

  const confirm = async () => {
    setState('busy');
    try {
      const data = await post('/commerce/swiggy/confirm', {
        action_id: card.id,
        ...(selectable ? { menu_item_id: selected } : {}),
      });
      if (data.status === 'awaiting_payment') {
        setPayment(data);
        setState('awaiting_payment');
        pollPayment(data.payment_action_id);
        return;
      }
      onResolved?.();
      trackOrder(data.action_id, data.external_order_id);
    } catch (e) {
      finish(e.message || 'Order could not be placed.');
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
      finish('Okay, not ordering.');
    }
  };

  if (state === 'tracking') {
    const pct = parseInt(tracking?.progress_percentage, 10);
    return (
      <div className="action-card">
        <div className="action-card-need">
          Order{tracking?.external_order_id ? ` #${tracking.external_order_id}` : ''} placed and being tracked live.
        </div>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{tracking?.title || 'Order placed'}</div>
        {tracking?.subtitle && <div className="action-card-need">{tracking.subtitle}</div>}
        {tracking?.eta_text && <div className="action-card-need">ETA: {tracking.eta_text}</div>}
        {Number.isFinite(pct) && (
          <div className="action-card-progress">
            <div className="action-card-progress-fill" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
          </div>
        )}
      </div>
    );
  }

  if (state === 'done') {
    return (
      <div className="action-card action-card-done">
        <FoodOrderIcon />
        <span>{result}</span>
      </div>
    );
  }

  if (state === 'awaiting_payment') {
    // Swiggy's place_food_order response carries no QR image at all --
    // bridgeUrl is a real https:// page Swiggy hosts itself (with
    // whatever real QR/UPI UI it renders), so this just links straight to
    // their own payment page rather than us trying to render anything.
    return (
      <div className="action-card">
        <div className="action-card-need">Pay to complete the order -- it's placed the moment payment completes.</div>
        {payment?.payment_link ? (
          <a className="btn" href={payment.payment_link} target="_blank" rel="noreferrer" style={{ display: 'block', textAlign: 'center' }}>
            Open payment page
          </a>
        ) : (
          <div className="action-card-need">No payment link was returned -- check the backend logs.</div>
        )}
        <div className="action-card-need" style={{ marginTop: 8 }}>Waiting for payment...</div>
      </div>
    );
  }

  return (
    <div className="action-card">
      <div className="action-card-need">{card.need}</div>
      <ul className="action-card-items">
        {card.items.map((item, i) => (
          <li key={i}>
            {selectable ? (
              <label className="action-card-item-choice">
                <input
                  type="radio"
                  name={`action-card-${card.id}`}
                  checked={selected === item.menu_item_id}
                  onChange={() => setSelected(item.menu_item_id)}
                />
                {item.label}
              </label>
            ) : (
              item.label
            )}
          </li>
        ))}
      </ul>
      <div className="action-card-buttons">
        <button className="btn" disabled={state === 'busy' || (selectable && !selected)} onClick={confirm}>
          <FoodOrderIcon /> Order {selectable ? 'selected' : 'this'}
        </button>
        <button className="btn secondary" disabled={state === 'busy'} onClick={dismiss}>
          Not hungry
        </button>
      </div>
    </div>
  );
}

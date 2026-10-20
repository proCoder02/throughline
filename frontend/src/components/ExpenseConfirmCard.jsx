import { useState } from 'react';
import { post } from '../api.js';
import TransactionConfirmModal from './TransactionConfirmModal.jsx';
import { ReceiptIcon } from '../icons.jsx';

// Web counterpart of the Flutter client's ExpenseConfirmCard
// (expense_confirm_card.dart) -- the "review before saving" card a
// global-chat reply carries as action_cards when the user states a spend
// in conversation rather than sharing a receipt (see app.py's
// _tool_log_expense). Nothing is written to `transactions` until the user
// confirms via the same TransactionConfirmModal a receipt upload uses.
export default function ExpenseConfirmCard({ card, onResolved }) {
  // 'pending' | 'busy' | 'saved' | 'dismissed' | 'error'
  const [state, setState] = useState(card._resolvedStatus || 'pending');
  const [wasDuplicate, setWasDuplicate] = useState(card._wasDuplicate || false);
  const [showModal, setShowModal] = useState(false);
  const [errorText, setErrorText] = useState(null);

  const save = async (edited) => {
    setShowModal(false);
    setState('busy');
    try {
      const data = await post('/transactions', edited);
      card._resolvedStatus = 'saved';
      card._wasDuplicate = !!data.duplicate;
      setWasDuplicate(!!data.duplicate);
      setState('saved');
      onResolved?.();
    } catch (e) {
      setErrorText('Could not save this expense.');
      setState('error');
    }
  };

  const discard = () => {
    card._resolvedStatus = 'dismissed';
    setState('dismissed');
  };

  if (state === 'saved') {
    return (
      <div className="action-card">
        <div className="action-card-done">{wasDuplicate ? 'Already logged -- looks like a duplicate.' : 'Expense saved.'}</div>
      </div>
    );
  }
  if (state === 'dismissed') {
    return <div className="action-card"><div className="action-card-done">Discarded.</div></div>;
  }
  if (state === 'error') {
    return <div className="action-card"><div className="action-card-done">{errorText || 'Something went wrong.'}</div></div>;
  }

  const busy = state === 'busy';
  const subtitleParts = [
    card.item,
    card.payee && `to ${card.payee}`,
    card.category && `(${card.category})`,
    card.payment_method && `via ${card.payment_method}`,
  ].filter(Boolean);

  return (
    <div className="action-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <ReceiptIcon />
        <span style={{ fontWeight: 600, fontSize: 14 }}>Rs {Number(card.amount || 0).toFixed(2)}</span>
      </div>
      {subtitleParts.length > 0 && (
        <div className="action-card-need" style={{ marginTop: 4 }}>{subtitleParts.join(' ')}</div>
      )}
      <div className="action-card-buttons" style={{ marginTop: 10 }}>
        <button className="btn" disabled={busy} onClick={() => setShowModal(true)}>Save</button>
        <button className="btn secondary" disabled={busy} onClick={discard}>Discard</button>
      </div>
      {showModal && (
        <TransactionConfirmModal transaction={card} onSave={save} onCancel={() => setShowModal(false)} />
      )}
    </div>
  );
}

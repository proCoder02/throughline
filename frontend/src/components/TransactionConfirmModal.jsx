import { useState } from 'react';
import { ReceiptIcon } from '../icons.jsx';

// Web counterpart of the Flutter client's showTransactionConfirmSheet
// (transaction_confirm_sheet.dart) -- shown right after a receipt/payment
// is extracted from an uploaded photo or PDF (see ChatsSection's
// sendGlobalImageMessage/sendGlobalPdfMessage), or when ExpenseConfirmCard's
// own Save button is tapped for a spend logged purely by voice/text. Lets
// the user fix a misread amount/payee/category before POST /transactions
// actually persists it. Returns the edited fields via onSave, or onCancel
// if discarded -- nothing is written here, the caller owns the actual save.
export default function TransactionConfirmModal({ transaction, onSave, onCancel }) {
  const [amount, setAmount] = useState(transaction.amount != null ? String(transaction.amount) : '');
  const [item, setItem] = useState(transaction.item || '');
  const [payee, setPayee] = useState(transaction.payee || '');
  const [category, setCategory] = useState(transaction.category || '');
  const [paymentMethod, setPaymentMethod] = useState(transaction.payment_method || '');

  const canSave = amount.trim() !== '' && !Number.isNaN(parseFloat(amount));

  const save = () => {
    if (!canSave) return;
    onSave({
      amount: parseFloat(amount),
      item: item.trim() || null,
      payee: payee.trim() || null,
      category: category.trim() || null,
      payment_method: paymentMethod.trim() || null,
    });
  };

  return (
    <div className="confirm-overlay" onClick={onCancel}>
      <div className="confirm-card" style={{ textAlign: 'left', maxHeight: '85vh', overflowY: 'auto' }} onClick={(e) => e.stopPropagation()}>
        <div className="confirm-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ReceiptIcon /> Save this payment?
        </div>
        <div className="confirm-message">Check the amount before it's saved to your spending.</div>

        <label className="hint" style={{ margin: '14px 0 0' }}>Amount</label>
        <input className="field" type="number" step="0.01" autoFocus value={amount} onChange={(e) => setAmount(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>What for (optional)</label>
        <input className="field" placeholder="e.g. Power bank" value={item} onChange={(e) => setItem(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Paid to</label>
        <input className="field" value={payee} onChange={(e) => setPayee(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Category</label>
        <input className="field" value={category} onChange={(e) => setCategory(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Paid via</label>
        <input className="field" value={paymentMethod} onChange={(e) => setPaymentMethod(e.target.value)} />

        <div className="confirm-actions">
          <button className="btn secondary" onClick={onCancel}>Discard</button>
          <button className="btn" disabled={!canSave} onClick={save}>Save</button>
        </div>
      </div>
    </div>
  );
}

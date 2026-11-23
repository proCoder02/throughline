// "You owe" on the web: money promised in conversations (server: pay/ --
// GET /pay/owed), with the people you pay below it (GET /pay/recent).
// Scanning QR codes is a phone thing, so the web offers what makes sense here:
//   Pay          -- opens the UPI app with the amount filled (phone browsers,
//                   when the person's UPI ID is known from an earlier payment)
//   Mark paid    -- records it and closes the promise
//   Not money I owe -- hides it (the task itself is untouched)
// Renders nothing until there's something to show, and nothing at all on a
// server without /pay (so it can't break Home).
import { useCallback, useEffect, useState } from 'react';
import { apiJson, post } from '../api.js';
import { showToast } from '../lib/notify.js';
import '../styles/pay.css';

const inr = (n) => '₹' + Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });
const IS_PHONE = typeof navigator !== 'undefined' && /Android|iPhone|iPad/i.test(navigator.userAgent);

// %20 for spaces (some UPI apps show a literal "+" from form encoding) and
// the @ in the UPI ID left as-is, the form UPI apps expect.
const enc = (v) => encodeURIComponent(v).replace(/%40/g, '@');
function upiLink(vpa, name, amount, note) {
  const q = [`pa=${enc(vpa)}`];
  if (name) q.push(`pn=${enc(name)}`);
  if (amount) q.push(`am=${Number(amount).toFixed(2)}`);
  q.push('cu=INR');
  if (note) q.push(`tn=${enc(note.slice(0, 50))}`);
  return `upi://pay?${q.join('&')}`;
}

export default function PayOwedCard({ compact = false }) {
  const [owed, setOwed] = useState(null);
  const [recent, setRecent] = useState([]);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    try {
      const [o, r] = await Promise.all([apiJson('/pay/owed'), apiJson('/pay/recent')]);
      setOwed(o.items || []);
      setRecent(r.payees || []);
    } catch {
      setOwed([]); // older server or offline: show nothing
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!owed || (owed.length === 0 && recent.length === 0)) return null;

  const markPaid = async (item) => {
    setBusy(item.task_id);
    try {
      const s = item.suggestion;
      if (s && item.amount) {
        await post('/pay/record', { vpa: s.vpa, payee_name: s.name, amount: item.amount, note: item.reason, app: 'UPI', task_id: item.task_id });
      } else {
        // No UPI ID known: keep the spending record when there's an amount, then close the promise.
        if (item.amount) await post('/transactions', { amount: item.amount, payee: item.person, item: item.reason });
        await post(`/tasks/${item.task_id}/complete`, {});
      }
      showToast('Marked paid. Promise closed.', 'success');
      setOwed((list) => list.filter((o) => o.task_id !== item.task_id));
      load();
    } catch (e) {
      showToast(e.message || "Couldn't save that. Try again.", 'error');
    } finally {
      setBusy(null);
    }
  };

  const dismiss = async (item) => {
    setOwed((list) => list.filter((o) => o.task_id !== item.task_id));
    try { await post(`/pay/owed/${item.task_id}/dismiss`, {}); } catch { load(); }
  };

  const rename = async (p) => {
    const label = window.prompt(`What do you call ${p.name}?`, p.name);
    if (label == null) return;
    try {
      await apiJson('/pay/payee', { method: 'PUT', body: JSON.stringify({ vpa: p.vpa, label }) });
      load();
    } catch (e) { showToast(e.message || "Couldn't save the name.", 'error'); }
  };

  return (
    <section className={'pay-card' + (compact ? ' compact' : '')} aria-labelledby="pay-owed-h">
      {owed.length > 0 && (
        <>
          <div className="pay-head">
            <h2 id="pay-owed-h">You owe</h2>
            <span className="pay-sub">From your conversations</span>
          </div>
          <ul className="pay-list">
            {owed.map((o) => (
              <li key={o.task_id} className="pay-item">
                <div className="pay-main">
                  <div className="pay-title">{o.person || 'Someone'}{o.reason ? ` · ${o.reason}` : ''}</div>
                  <div className="pay-meta">{o.text}{o.source_title ? ` · ${o.source_title}` : ''}{o.due ? ` · due ${o.due}` : ''}</div>
                </div>
                {o.amount != null && <div className="pay-amount">{inr(o.amount)}</div>}
                <div className="pay-actions">
                  {IS_PHONE && o.suggestion && o.amount != null && (
                    <a className="pay-btn primary" href={upiLink(o.suggestion.vpa, o.suggestion.name, o.amount, o.reason)}>Pay</a>
                  )}
                  <button type="button" className="pay-btn" disabled={busy === o.task_id} onClick={() => markPaid(o)}>
                    {busy === o.task_id ? 'Saving…' : 'Mark paid'}
                  </button>
                  <button type="button" className="pay-link" onClick={() => dismiss(o)}>Not money I owe</button>
                </div>
              </li>
            ))}
          </ul>
          {!IS_PHONE && <p className="pay-note">To pay by scanning a QR code, use Pay in the Throughline phone app.</p>}
        </>
      )}
      {recent.length > 0 && !compact && (
        <>
          <div className="pay-head"><h3>People you pay</h3></div>
          <ul className="pay-people">
            {recent.slice(0, 6).map((p) => (
              <li key={p.vpa}>
                <span className="pay-avatar" aria-hidden="true">{(p.name || '?')[0].toUpperCase()}</span>
                <span className="pay-person">
                  <span className="pay-title">{p.name}</span>
                  <span className="pay-meta">Last {inr(p.last_amount)}{p.last_note ? ` · ${p.last_note}` : ''}</span>
                </span>
                <button type="button" className="pay-link" onClick={() => rename(p)}>Rename</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

import { useEffect, useRef, useState } from 'react';
import { subscribeToasts, dismissToast, subscribeConfirm } from '../lib/notify.js';
import { useFocusTrap } from '../hooks/useFocusTrap.js';
import { CheckIcon, CloseIcon, InfoIcon } from '../icons.jsx';

const TOAST_ICONS = { success: CheckIcon, error: CloseIcon, info: InfoIcon };

/// Mounted once (see App.jsx) -- the only thing that actually renders
/// toasts/the confirm dialog; every other component just calls
/// showToast()/confirmDialog() from lib/notify.js without knowing this
/// exists. Replaces window.alert()/window.confirm() everywhere in the app
/// so destructive/error UI matches the theme instead of native browser chrome.
export default function NotificationHost() {
  const [toasts, setToasts] = useState([]);
  const [confirmState, setConfirmState] = useState(null);
  const confirmCardRef = useRef(null);

  useEffect(() => subscribeToasts(setToasts), []);
  useEffect(() => subscribeConfirm((request) => setConfirmState(request)), []);

  const resolveConfirm = (result) => {
    confirmState?.resolve(result);
    setConfirmState(null);
  };

  // Blocks the rest of the page while open, so Tab must not be able to
  // escape into the dimmed content behind it -- Escape is a real cancel,
  // same as clicking outside would be on most native dialogs.
  useFocusTrap(!!confirmState, confirmCardRef, () => resolveConfirm(false));

  return (
    <>
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((t) => {
          const Icon = TOAST_ICONS[t.type] || TOAST_ICONS.info;
          return (
            <button key={t.id} className={'toast toast-' + t.type} onClick={() => dismissToast(t.id)} title="Dismiss">
              <Icon />
              <span>{t.message}</span>
            </button>
          );
        })}
      </div>

      {confirmState && (
        <div className="confirm-overlay" role="alertdialog" aria-modal="true" aria-label={confirmState.title || 'Confirm'}>
          <div className="confirm-card" ref={confirmCardRef}>
            {confirmState.title && <div className="confirm-title">{confirmState.title}</div>}
            <div className="confirm-message">{confirmState.message}</div>
            <div className="confirm-actions">
              <button className="btn secondary" onClick={() => resolveConfirm(false)}>
                {confirmState.cancelLabel}
              </button>
              <button className={'btn' + (confirmState.danger ? ' danger' : '')} onClick={() => resolveConfirm(true)}>
                {confirmState.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

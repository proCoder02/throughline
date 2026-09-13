// Themed replacement for window.alert()/window.confirm() -- a tiny pub/sub
// singleton rather than React Context, so any component can call
// showToast()/confirmDialog() directly without needing a Provider wrapped
// around it. NotificationHost (mounted once in App.jsx) is the only
// subscriber that actually renders anything.
let toasts = [];
let toastListeners = [];
let nextToastId = 1;

function emitToasts() {
  toastListeners.forEach((fn) => fn(toasts));
}

export function showToast(message, type = 'info', duration = 4000) {
  const id = nextToastId++;
  toasts = [...toasts, { id, message, type }];
  emitToasts();
  setTimeout(() => dismissToast(id), duration);
  return id;
}

export function dismissToast(id) {
  toasts = toasts.filter((t) => t.id !== id);
  emitToasts();
}

export function subscribeToasts(listener) {
  toastListeners.push(listener);
  listener(toasts);
  return () => { toastListeners = toastListeners.filter((l) => l !== listener); };
}

let confirmListener = null;

export function subscribeConfirm(listener) {
  confirmListener = listener;
  return () => { if (confirmListener === listener) confirmListener = null; };
}

/// Promise-based confirm -- `if (!(await confirmDialog({...}))) return;` in
/// place of `if (!confirm(...)) return;`. Falls back to the native confirm
/// if NotificationHost somehow isn't mounted yet, so this never silently
/// no-ops a destructive-action guard.
export function confirmDialog({ title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false }) {
  return new Promise((resolve) => {
    if (!confirmListener) {
      resolve(window.confirm(message));
      return;
    }
    confirmListener({ title, message, confirmLabel, cancelLabel, danger, resolve });
  });
}

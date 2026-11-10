// Mounted once for the whole app (Personal and Corporate): shows who just
// messaged at work -- a toast you can click, a desktop notification when the
// tab is in the background, and the unread count in the tab title.
import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { refreshUnread, useCorpUnread } from '../lib/corpLive.js';

export default function CorpAlerts() {
  const navigate = useNavigate();
  const { total } = useCorpUnread();
  const [toast, setToast] = useState(null);

  useEffect(() => {
    refreshUnread();
    const onFocus = () => refreshUnread();
    window.addEventListener('focus', onFocus);
    const t = setInterval(refreshUnread, 20000); // fallback when the live socket is down
    return () => { window.removeEventListener('focus', onFocus); clearInterval(t); };
  }, []);

  useEffect(() => {
    const open = (m) => { window.focus(); navigate(`/app/corp/messages?t=${m.thread_id}`); setToast(null); };
    const onEvent = (e) => {
      const m = e.detail;
      if (m.type !== 'corp_message') return;
      const here = window.location.pathname.startsWith('/app/corp/messages')
        && new URLSearchParams(window.location.search).get('t') === String(m.thread_id);
      if (here && document.visibilityState === 'visible') return;
      const title = m.team ? `${m.sender} in ${m.title}` : m.sender;
      if (document.visibilityState !== 'visible' && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
        const n = new Notification(title, { body: m.preview, tag: `corp-${m.thread_id}` });
        n.onclick = () => { open(m); n.close(); };
      }
      setToast({ ...m, heading: title });
    };
    window.addEventListener('tl:corp', onEvent);
    return () => window.removeEventListener('tl:corp', onEvent);
  }, [navigate]);

  // Opening the conversation the toast is about makes it redundant.
  const location = useLocation();
  useEffect(() => {
    if (toast && location.pathname.startsWith('/app/corp/messages')
      && new URLSearchParams(location.search).get('t') === String(toast.thread_id)) setToast(null);
  }, [location, toast]);

  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 7000);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    const base = document.title.replace(/^\(\d+\+?\) /, '');
    document.title = total > 0 ? `(${total > 99 ? '99+' : total}) ${base}` : base;
  }, [total]);

  if (!toast) return null;
  return (
    <button className="corp-alert" role="status" onClick={() => { window.focus(); navigate(`/app/corp/messages?t=${toast.thread_id}`); setToast(null); }}>
      <span className="corp-alert-title">{toast.heading}</span>
      <span className="corp-alert-body">{toast.preview}</span>
      <span className="corp-alert-hint">Open</span>
    </button>
  );
}

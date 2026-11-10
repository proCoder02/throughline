// Colleague chat: team threads and direct messages. Polls for new messages
// (every 4 s in the open thread, 15 s for the list) -- simple and enough here.
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { SendIcon, SparkleIcon } from '../../../icons.jsx';
import { CommonGroundCard, fmtDate } from '../components/work.jsx';
import { refreshUnread } from '../../../lib/corpLive.js';

function Thread({ corp, threadId, user, onRead }) {
  const orgId = corp.data.org.id;
  const [msgs, setMsgs] = useState(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const last = useRef(0);
  const end = useRef(null);

  const add = (list) => {
    if (!list.length) return;
    last.current = list[list.length - 1].id;
    setMsgs((m) => [...(m || []), ...list.filter((x) => !(m || []).some((y) => y.id === x.id))]);
    corpApi.read(orgId, threadId, last.current).then(() => { onRead(); refreshUnread(); }).catch(() => {});
  };
  const poll = useCallback(() => corpApi.messages(orgId, threadId, last.current).then((d) => add(d.messages)).catch(() => {}),
    [orgId, threadId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    last.current = 0; setMsgs(null);
    corpApi.messages(orgId, threadId).then((d) => { setMsgs([]); add(d.messages); }).catch((e) => showToast(e.message, 'error'));
    // Live events bring new messages at once; the slow poll is only a fallback.
    const onEvent = (e) => { if (e.detail.thread_id === threadId) poll(); };
    window.addEventListener('tl:corp', onEvent);
    const t = setInterval(poll, 20000);
    return () => { clearInterval(t); window.removeEventListener('tl:corp', onEvent); };
  }, [orgId, threadId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [msgs?.length]);

  const send = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    try { add((await corpApi.send(orgId, threadId, text)).messages); setText(''); } catch (err) { showToast(err.message, 'error'); }
    finally { setBusy(false); }
  };
  const commonGround = async () => {
    setBusy(true);
    try { await corpApi.threadCommonGround(orgId, threadId); await poll(); } catch (err) { showToast(err.message, 'error'); }
    finally { setBusy(false); }
  };

  return (
    <section className="corp-thread">
      <div className="corp-thread-tools">
        <button className="corp-btn small" onClick={commonGround} disabled={busy}><SparkleIcon aria-hidden="true" /> Find common ground</button>
      </div>
      <div className="corp-msgs" aria-live="polite">
        {!msgs ? <div className="corp-spinner small" aria-label="Loading" />
          : msgs.length === 0 ? <p className="corp-muted corp-pad">No messages yet. Say hello.</p>
          : msgs.map((m) => (m.kind === 'common_ground'
            ? <CommonGroundCard key={m.id} corp={corp} id={m.meta.id} result={m.meta.result} usedNotes={m.meta.used_notes} />
            : (
              <div key={m.id} className={'corp-msg' + (m.sender_id === user.id ? ' mine' : '')}>
                {m.sender_id !== user.id && <div className="corp-msg-who">{m.sender}</div>}
                <div className="corp-msg-body">{m.body}</div>
                <div className="corp-msg-time">{fmtDate(m.created_at)} {new Date(m.created_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}</div>
              </div>
            )))}
        <div ref={end} />
      </div>
      <form className="corp-composer" onSubmit={send}>
        <input aria-label="Message" placeholder="Write a message" value={text} onChange={(e) => setText(e.target.value)} maxLength={4000} />
        <button className="corp-btn primary" disabled={busy || !text.trim()} aria-label="Send"><SendIcon /></button>
      </form>
    </section>
  );
}

export default function MessagesPage({ corp, user }) {
  const [params, setParams] = useSearchParams();
  const [threads, setThreads] = useState(null);
  const orgId = corp.data.org.id;
  const load = useCallback(() => corpApi.threads(orgId).then((d) => setThreads(d.threads)).catch(() => setThreads([])), [orgId]);
  useEffect(() => {
    load();
    window.addEventListener('tl:corp', load);
    const t = setInterval(load, 30000);
    return () => { clearInterval(t); window.removeEventListener('tl:corp', load); };
  }, [load]);
  const open = Number(params.get('t')) || null;
  const current = threads?.find((t) => t.id === open);

  return (
    <div className="corp-page wide">
      <header className="corp-page-head"><div><h1>Messages</h1>
        <p className="corp-muted">Your teams’ threads and direct messages. Promises made here are picked up into Commitments.</p></div></header>
      <div className={'corp-split' + (open ? ' has-selection' : '')}>
        <div className="corp-card corp-tree-card">
          {!threads ? <div className="corp-spinner small" aria-label="Loading" />
            : threads.length === 0 ? <p className="corp-muted corp-pad">Join a team, or message someone from People.</p>
            : (
              <ul className="corp-tree">{threads.map((t) => (
                <li key={t.id}>
                  <button className={'corp-tree-row' + (t.id === open ? ' active' : '')} onClick={() => setParams({ t: String(t.id) })}>
                    <span className="corp-tree-name">{t.kind === 'team' ? '# ' : ''}{t.name}
                      {t.last_body && <span className="corp-row-sub corp-ellipsis">{t.last_body}</span>}</span>
                    {t.unread > 0 && <span className="corp-unread">{t.unread}</span>}
                  </button>
                </li>))}
              </ul>
            )}
        </div>
        {current ? (
          <div className="corp-panel">
            <header className="corp-panel-head"><h2>{current.kind === 'team' ? '# ' : ''}{current.name}</h2></header>
            <Thread key={current.id} corp={corp} threadId={current.id} user={user} onRead={load} />
          </div>
        ) : <div className="corp-card corp-panel-empty"><p className="corp-muted">Pick a conversation.</p></div>}
      </div>
    </div>
  );
}

// Ask about your teams' history. Only content from teams you're in is used.
// Same look and conventions as Personal's "Ask Throughline" (AssistantChat):
// centered reading column, greeting + starter cards when empty, auto-growing
// composer (Enter sends, Shift+Enter newline), "thinking" state, inline error
// with Retry, Copy on every answer.
// Conversations are threads saved on the server (yours only, also on the
// phone): "New chat" keeps the old one in the Chats list, any of them can be
// reopened or deleted, and a follow-up ("and who owns it?") knows what came
// before in its thread.
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { confirmDialog, showToast } from '../../../lib/notify.js';
import { myUnits } from '../components/work.jsx';
import FormattedText from '../../../components/FormattedText.jsx';
import { ArrowUpIcon, ArrowDownIcon, CheckIcon, CloseIcon, CopyIcon, MenuIcon, PlusIcon, RetryIcon, SparkleIcon, TrashIcon } from '../../../icons.jsx';
import '../../../components/assistant/assistant.css';

const LINK = { meeting: (s) => `/app/corp/meetings/${s.id}`, decision: (s) => `/app/corp/decisions?d=${s.id}`, ticket: (s) => `/app/corp/tickets/${s.id}`,
  commitment: () => '/app/corp/commitments', message: (s) => `/app/corp/messages?t=${s.id}` };
const KIND = { meeting: 'Meeting', decision: 'Decision', commitment: 'Commitment', message: 'Chat', ticket: 'Ticket' };
const STARTERS = [
  { title: 'Decisions', prompt: 'What did we decide about the database migration?' },
  { title: 'Pending', prompt: 'What is still pending from our last meetings?' },
  { title: 'This week', prompt: 'Who owes what this week?' },
  { title: 'Risks', prompt: 'Which commitments are overdue or at risk?' },
];
const NEAR_BOTTOM_PX = 120;
const isPhone = () => window.innerWidth < 860;

let nextId = 1;
const withId = (m) => ({ id: nextId++, ...m });
// Which thread was open, so coming back to Ask reopens it (this tab only).
const openKey = (orgId) => `corp-ask-open:${orgId}`;
const remember = (orgId, threadId) => {
  try { if (threadId) sessionStorage.setItem(openKey(orgId), String(threadId)); else sessionStorage.removeItem(openKey(orgId)); } catch { /* storage blocked */ }
};
const recalled = (orgId) => { try { return Number(sessionStorage.getItem(openKey(orgId))) || null; } catch { return null; } };

function when(iso) {
  const d = new Date(iso);
  const today = new Date();
  if (d.toDateString() === today.toDateString()) return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const y = new Date(today); y.setDate(today.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return 'Yesterday';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text || ''); setCopied(true); setTimeout(() => setCopied(false), 1500); }
    catch { showToast('Could not copy -- your browser blocked clipboard access.', 'error'); }
  };
  return (
    <button type="button" className="ac-icon-btn" onClick={copy} title={copied ? 'Copied' : 'Copy'} aria-label={copied ? 'Copied' : 'Copy answer'}>
      {copied ? <CheckIcon /> : <CopyIcon />}
    </button>
  );
}

const AssistantTurn = memo(function AssistantTurn({ message, onRetry }) {
  return (
    <div className={'ac-turn ac-assistant' + (message.error ? ' is-error' : '')}>
      <div className="ac-avatar" aria-hidden="true"><SparkleIcon /></div>
      <div className="ac-assistant-body">
        {message.error ? (
          <div className="ac-error">
            <span>{message.content}</span>
            <button type="button" className="ac-retry" onClick={() => onRetry(message)}><RetryIcon /> Retry</button>
          </div>
        ) : <FormattedText text={message.content} />}
        {message.sources?.length > 0 && (
          <div className="corp-ask-sources" aria-label="Sources">
            {message.sources.map((s) => (
              <Link key={s.ref} className="corp-ask-source" to={LINK[s.type]?.(s) || '/app/corp'} title={s.title}>
                <span className="corp-ask-source-kind">{KIND[s.type] || s.type} · {s.ref}</span>
                <span className="corp-ask-source-title">{s.title}</span>
              </Link>
            ))}
          </div>
        )}
        {!message.error && message.content && <div className="ac-actions"><CopyButton text={message.content} /></div>}
      </div>
    </div>
  );
});

function ChatList({ threads, current, onOpen, onNew, onDelete, onClose }) {
  return (
    <nav className="corp-ask-history" aria-label="Your Ask conversations">
      <div className="corp-ask-history-head">
        <button type="button" className="corp-btn primary corp-ask-new" onClick={onNew}><PlusIcon aria-hidden="true" /> New chat</button>
        {onClose && <button type="button" className="corp-icon-btn" onClick={onClose} aria-label="Close chats"><CloseIcon /></button>}
      </div>
      <div className="corp-ask-history-label">Chats</div>
      {threads === null ? <div className="corp-spinner small" aria-label="Loading" />
        : threads.length === 0 ? <p className="corp-muted corp-small corp-ask-history-empty">Your conversations will be kept here.</p>
        : (
          <ul>{threads.map((t) => (
            <li key={t.id} className={t.id === current ? 'active' : ''}>
              <button type="button" className="corp-ask-history-item" onClick={() => onOpen(t.id)} aria-current={t.id === current ? 'true' : undefined}>
                <span className="corp-ask-history-title">{t.title || 'Untitled'}</span>
                <span className="corp-ask-history-meta">{when(t.updated_at)}{t.questions > 1 ? ` · ${t.questions} questions` : ''}</span>
              </button>
              <button type="button" className="corp-icon-btn danger corp-ask-history-del" onClick={() => onDelete(t)}
                aria-label={`Delete “${t.title}”`} title="Delete"><TrashIcon /></button>
            </li>))}
          </ul>
        )}
    </nav>
  );
}

export default function AskPage({ corp }) {
  const orgId = corp.data.org.id;
  const units = myUnits(corp);
  const [unitId, setUnitId] = useState(null);
  const [threads, setThreads] = useState(null);
  const [threadId, setThreadId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loadingThread, setLoadingThread] = useState(false);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [showJump, setShowJump] = useState(false);
  const [listOpen, setListOpen] = useState(false); // phone: the Chats panel
  const scrollRef = useRef(null);
  const textareaRef = useRef(null);
  const stickToBottom = useRef(true);
  const current = useRef(null); // the thread on screen, so a late answer never lands in another chat
  current.current = threadId;

  const loadThreads = useCallback(() => corpApi.askThreads(orgId).then((d) => setThreads(d.threads)).catch(() => setThreads([])), [orgId]);

  const openThread = useCallback(async (id) => {
    setListOpen(false);
    if (!id) return;
    setThreadId(id); remember(orgId, id);
    setLoadingThread(true); setMessages([]);
    stickToBottom.current = true;
    try {
      const d = await corpApi.askThread(orgId, id);
      if (current.current !== id) return;
      setMessages(d.messages.map((m) => withId({ role: m.role, content: m.content, sources: m.sources, scope: m.unit_name })));
    } catch (e) {
      showToast(e.message, 'error'); setThreadId(null); remember(orgId, null);
    } finally { setLoadingThread(false); }
  }, [orgId]);

  useEffect(() => {
    loadThreads();
    const last = recalled(orgId);
    if (last) openThread(last);
  }, [orgId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!isPhone()) textareaRef.current?.focus(); }, [threadId]);

  const newChat = () => {
    setThreadId(null); remember(orgId, null); setMessages([]); setListOpen(false);
    textareaRef.current?.focus();
  };

  const deleteThread = async (t) => {
    if (!(await confirmDialog({ title: `Delete “${t.title}”?`, danger: true, confirmLabel: 'Delete', message: 'This conversation is removed for good.' }))) return;
    try {
      await corpApi.deleteAskThread(orgId, t.id);
      if (t.id === threadId) newChat();
      loadThreads();
    } catch (e) { showToast(e.message, 'error'); }
  };

  const scrollToBottom = useCallback((smooth = true) => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }, []);
  const scrolledOnce = useRef(false);
  useLayoutEffect(() => {
    if (!stickToBottom.current) return;
    scrollToBottom(scrolledOnce.current);
    scrolledOnce.current = true;
  }, [messages, sending, scrollToBottom]);
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    stickToBottom.current = near;
    setShowJump(!near);
  };

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }, [input]);

  const append = (...items) => setMessages((m) => [...m, ...items.map(withId)]);

  const askBackend = async (question, scope) => {
    stickToBottom.current = true;
    setSending(true);
    const asked = current.current;
    try {
      const res = await corpApi.ask(orgId, question, scope, asked);
      if (current.current !== asked) { loadThreads(); return; } // switched chats meanwhile: it's saved in its own thread
      if (!asked) { setThreadId(res.thread_id); remember(orgId, res.thread_id); }
      append({ role: 'assistant', content: res.answer || 'No answer.', sources: res.sources || [] });
      loadThreads();
    } catch (e) {
      if (current.current === asked) {
        append({ role: 'assistant', content: "Couldn't get an answer -- " + (e.message || 'network error') + '.', error: true, retryPrompt: question, retryUnit: scope });
      }
    } finally {
      setSending(false);
    }
  };

  const submit = (text = input) => {
    const q = text.trim().slice(0, 500);
    if (!q || sending) return;
    setInput('');
    const unit = units.find((u) => u.id === unitId);
    append({ role: 'user', content: q, scope: unit?.name });
    askBackend(q, unitId);
  };

  const retryRef = useRef(null);
  retryRef.current = (failed) => {
    if (sending) return;
    setMessages((m) => m.filter((x) => x.id !== failed.id));
    askBackend(failed.retryPrompt, failed.retryUnit ?? null);
  };
  const onRetry = useCallback((failed) => retryRef.current(failed), []);

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); }
  };

  const isEmpty = messages.length === 0 && !sending && !loadingThread;
  const scopeName = units.find((u) => u.id === unitId)?.name;
  const title = threads?.find((t) => t.id === threadId)?.title;

  const list = (onClose) => (
    <ChatList threads={threads} current={threadId} onOpen={openThread} onNew={newChat} onDelete={deleteThread} onClose={onClose} />
  );

  return (
    <div className="corp-ask-layout">
      <div className="corp-ask-side">{list(null)}</div>
      {listOpen && (
        <div className="corp-ask-drawer" role="dialog" aria-modal="true" aria-label="Chats" onClick={(e) => e.target === e.currentTarget && setListOpen(false)}>
          {list(() => setListOpen(false))}
        </div>
      )}
      <div className={'ac corp-ask' + (isEmpty ? ' is-empty' : '')}>
        <header className="ac-header">
          <button type="button" className="corp-icon-btn corp-ask-menu" onClick={() => setListOpen(true)} aria-label="Your chats"><MenuIcon /></button>
          <div className="ac-header-text">
            <div className="ac-title">{title || 'Ask'}</div>
            <div className="ac-subtitle">Answers come only from meetings, decisions, commitments and chat in teams you’re in.</div>
          </div>
          <div className="corp-ask-head-actions">
            <label className="corp-ask-scope">
              <span>Team</span>
              <select value={unitId ?? ''} aria-label="Which teams to search"
                onChange={(e) => setUnitId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">All my teams</option>
                {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </label>
            {(messages.length > 0 || threadId) && (
              <button type="button" className="corp-btn corp-ask-new-inline" onClick={newChat} disabled={sending}>New chat</button>
            )}
          </div>
        </header>

        <div className="ac-scroll" ref={scrollRef} onScroll={onScroll}>
          <div className="ac-column">
            {loadingThread && <div className="ac-loading" aria-busy="true"><span className="ac-skeleton" /><span className="ac-skeleton short" /><span className="ac-skeleton" /></div>}
            {isEmpty && (
              <div className="ac-welcome">
                <div className="ac-welcome-mark" aria-hidden="true"><SparkleIcon /></div>
                <h2>What do you want to know about your work?</h2>
                <p>Ask across your teams’ meetings, decisions, commitments, tickets and chat. Every answer links to where it came from.</p>
                <div className="ac-starters">
                  {STARTERS.map((s) => (
                    <button type="button" key={s.prompt} className="ac-starter" onClick={() => { setInput(s.prompt); textareaRef.current?.focus(); }}>
                      <span className="ac-starter-title">{s.title}</span>
                      <span className="ac-starter-prompt">{s.prompt}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div role="log" aria-live="polite" aria-relevant="additions">
              {messages.map((m) => (m.role === 'user' ? (
                <div key={m.id} className="ac-turn ac-user">
                  <div className="ac-user-bubble">
                    <span className="ac-user-text">{m.content}</span>
                    {m.scope && <span className="corp-ask-scope-tag">in {m.scope}</span>}
                  </div>
                </div>
              ) : <AssistantTurn key={m.id} message={m} onRetry={onRetry} />))}
            </div>

            {sending && (
              <div className="ac-turn ac-assistant" aria-live="polite">
                <div className="ac-avatar is-thinking" aria-hidden="true"><SparkleIcon /></div>
                <div className="ac-thinking"><span className="ac-dots" aria-hidden="true"><i /><i /><i /></span>Searching your teams’ history…</div>
              </div>
            )}
          </div>
        </div>

        {showJump && !isEmpty && (
          <button type="button" className="ac-jump" onClick={() => { stickToBottom.current = true; scrollToBottom(); }} aria-label="Jump to latest"><ArrowDownIcon /></button>
        )}

        <div className="ac-composer-wrap">
          <div className={'ac-composer' + (sending ? ' is-busy' : '')}>
            <div className="ac-composer-row">
              <textarea ref={textareaRef} rows={1} value={input} maxLength={500}
                onChange={(e) => setInput(e.target.value)} onKeyDown={onKeyDown}
                placeholder={threadId ? 'Ask a follow-up…' : scopeName ? `Ask about ${scopeName}…` : 'Ask about your team’s history…'} aria-label="Ask about your teams" />
              <button type="button" className="ac-send" onClick={() => submit()} disabled={sending || !input.trim()}
                title={sending ? 'Waiting for the answer…' : 'Send (Enter)'} aria-label="Send question"><ArrowUpIcon /></button>
            </div>
          </div>
          <div className="ac-disclaimer">Follow-ups use this chat’s earlier questions. Throughline can make mistakes, so check the sources.</div>
        </div>
      </div>
    </div>
  );
}

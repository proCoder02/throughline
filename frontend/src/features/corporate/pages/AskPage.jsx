// Ask about your teams' history. Only content from teams you're in is used.
// Same look and conventions as Personal's "Ask Throughline" (AssistantChat):
// centered reading column, greeting + starter cards when empty, auto-growing
// composer (Enter sends, Shift+Enter newline), "thinking" state, inline error
// with Retry, Copy on every answer. Each question is answered on its own by
// /ask; the thread is kept for this browser session so it survives moving
// between Corporate pages.
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { myUnits } from '../components/work.jsx';
import FormattedText from '../../../components/FormattedText.jsx';
import { ArrowUpIcon, ArrowDownIcon, CheckIcon, CopyIcon, RetryIcon, SparkleIcon } from '../../../icons.jsx';
import '../../../components/assistant/assistant.css';

const LINK = { meeting: (s) => `/app/corp/meetings/${s.id}`, decision: (s) => `/app/corp/decisions?d=${s.id}`,
  commitment: () => '/app/corp/commitments', message: (s) => `/app/corp/messages?t=${s.id}` };
const KIND = { meeting: 'Meeting', decision: 'Decision', commitment: 'Commitment', message: 'Chat' };
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
const storeKey = (orgId) => `corp-ask-thread:${orgId}`;
function loadThread(orgId) {
  try { return (JSON.parse(sessionStorage.getItem(storeKey(orgId))) || []).map(withId); } catch { return []; }
}
function saveThread(orgId, messages) {
  try { sessionStorage.setItem(storeKey(orgId), JSON.stringify(messages.slice(-40).map(({ id, ...m }) => m))); } catch { /* storage blocked */ }
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

export default function AskPage({ corp }) {
  const orgId = corp.data.org.id;
  const units = myUnits(corp);
  const [unitId, setUnitId] = useState(null);
  const [messages, setMessages] = useState(() => loadThread(orgId));
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [showJump, setShowJump] = useState(false);
  const scrollRef = useRef(null);
  const textareaRef = useRef(null);
  const stickToBottom = useRef(true);

  useEffect(() => { saveThread(orgId, messages); }, [orgId, messages]);
  useEffect(() => { if (!isPhone()) textareaRef.current?.focus(); }, []);

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
    try {
      const res = await corpApi.ask(orgId, question, scope);
      append({ role: 'assistant', content: res.answer || 'No answer.', sources: res.sources || [] });
    } catch (e) {
      append({ role: 'assistant', content: "Couldn't get an answer -- " + (e.message || 'network error') + '.', error: true, retryPrompt: question, retryUnit: scope });
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

  const isEmpty = messages.length === 0 && !sending;
  const scopeName = units.find((u) => u.id === unitId)?.name;

  return (
    <div className={'ac corp-ask' + (isEmpty ? ' is-empty' : '')}>
      <header className="ac-header">
        <div className="ac-header-text">
          <div className="ac-title">Ask</div>
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
          {messages.length > 0 && (
            <button type="button" className="corp-btn" onClick={() => { setMessages([]); textareaRef.current?.focus(); }} disabled={sending}>New chat</button>
          )}
        </div>
      </header>

      <div className="ac-scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="ac-column">
          {isEmpty && (
            <div className="ac-welcome">
              <div className="ac-welcome-mark" aria-hidden="true"><SparkleIcon /></div>
              <h2>What do you want to know about your work?</h2>
              <p>Ask across your teams’ meetings, decisions, commitments and chat. Every answer links to where it came from.</p>
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
              placeholder={scopeName ? `Ask about ${scopeName}…` : 'Ask about your team’s history…'} aria-label="Ask about your teams" />
            <button type="button" className="ac-send" onClick={() => submit()} disabled={sending || !input.trim()}
              title={sending ? 'Waiting for the answer…' : 'Send (Enter)'} aria-label="Send question"><ArrowUpIcon /></button>
          </div>
        </div>
        <div className="ac-disclaimer">Each question is answered on its own. Throughline can make mistakes, so check the sources.</div>
      </div>
    </div>
  );
}

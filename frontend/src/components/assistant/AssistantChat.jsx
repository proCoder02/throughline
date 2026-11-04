import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { apiJson, post, postForm } from '../../api.js';
import { showToast } from '../../lib/notify.js';
import { maybeGetLocation } from '../../lib/location.js';
import { useMailStatus, pendingReplyLabel } from '../../lib/mail.js';
import { FOLLOWUP_PENDING_LABEL, looksLikeFollowUp } from '../../lib/followup.js';
import FormattedText from '../FormattedText.jsx';
import ActionCard from '../ActionCard.jsx';
import MailReplyMeta from '../MailReplyMeta.jsx';
import MailSuggestions, { hasMailReply } from '../MailSuggestions.jsx';
import AttachMenu from '../AttachMenu.jsx';
import TransactionConfirmModal from '../TransactionConfirmModal.jsx';
import PolicyConfirmModal from '../PolicyConfirmModal.jsx';
import {
  AttachIcon, ArrowUpIcon, ArrowDownIcon, BackIcon, CloseIcon, CopyIcon, CheckIcon, RetryIcon, SparkleIcon, PdfIcon,
} from '../../icons.jsx';
import './assistant.css';

// The global "Ask" assistant, ChatGPT-style, shared by both web surfaces --
// the desktop OS screen's Ask tab and the phone-web chat (ChatsSection).
// Built around the usual generative-AI chat conventions:
// - one centered reading column; the assistant's answer is plain document
//   text (no bubble) so long formatted replies read like a page, while the
//   user's turns are small right-aligned pills;
// - an empty state that is a greeting + a few starter prompts, with the
//   composer right there in the middle -- not a blank thread;
// - an auto-growing composer: Enter sends, Shift+Enter is a newline,
//   typing stays possible while a reply is pending;
// - a visible "working" state that says what it's doing, an inline error
//   with Retry instead of a dead "Request failed.", Copy on every answer;
// - auto-scroll that never yanks the view away from someone reading
//   further up -- a "jump to latest" button appears instead;
// - a standing reminder that answers can be wrong.
// Same backend endpoints/behaviors the two old screens used (text, photo
// with a description, PDF; action cards; transaction/policy confirm sheets).

const PHONE_BREAKPOINT_PX = 860;
const isPhone = () => window.innerWidth < PHONE_BREAKPOINT_PX;
const NEAR_BOTTOM_PX = 120;

const STARTERS = [
  { title: 'Commitments', prompt: 'What commitments have I made recently?' },
  { title: 'Catch up', prompt: "Who haven't I spoken to in a while?" },
  { title: 'My week', prompt: 'Summarize my week.' },
  { title: 'Spending', prompt: 'Where am I spending the most this month?' },
];

let nextId = 1;
const withId = (m) => ({ id: nextId++, ...m });

function errorText(data) {
  if (!data?.error) return null;
  return typeof data.error === 'string' ? data.error : data.error.error || 'Something went wrong.';
}

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text || '');
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      showToast('Could not copy -- your browser blocked clipboard access.', 'error');
    }
  };
  return (
    <button type="button" className="ac-icon-btn" onClick={copy} title={copied ? 'Copied' : 'Copy'} aria-label={copied ? 'Copied' : 'Copy answer'}>
      {copied ? <CheckIcon /> : <CopyIcon />}
    </button>
  );
}

// memo: typing in the composer re-renders the screen, and without this every
// turn's markdown was re-parsed on each keystroke.
const AssistantTurn = memo(function AssistantTurn({ message, onRetry, onActionResolved }) {
  return (
    <div className={'ac-turn ac-assistant' + (message.error ? ' is-error' : '')}>
      <div className="ac-avatar" aria-hidden="true"><SparkleIcon /></div>
      <div className="ac-assistant-body">
        {message.error ? (
          <div className="ac-error">
            <span>{message.content}</span>
            {message.retryPrompt && (
              <button type="button" className="ac-retry" onClick={() => onRetry(message)}>
                <RetryIcon /> Retry
              </button>
            )}
          </div>
        ) : (
          <FormattedText text={message.content} />
        )}
        <MailReplyMeta meta={message.replyMeta} />
        {message.actionCards?.map((card, i) => (
          <ActionCard key={card.draft_id ?? card.id ?? i} card={card} onResolved={onActionResolved} />
        ))}
        {!message.error && message.content && (
          <div className="ac-actions">
            <CopyButton text={message.content} />
          </div>
        )}
      </div>
    </div>
  );
});

const UserTurn = memo(function UserTurn({ message }) {
  return (
    <div className="ac-turn ac-user">
      <div className="ac-user-bubble">
        {message.imageUrl && <img src={message.imageUrl} alt="Attached" className="ac-user-image" />}
        {message.fileName && <span className="ac-user-file"><PdfIcon /> {message.fileName}</span>}
        {message.content && <span className="ac-user-text">{message.content}</span>}
      </div>
    </div>
  );
});

// History arrives a page at a time, newest first; older pages load as the
// reader scrolls up.
const HISTORY_PAGE = 50;
const LOAD_OLDER_PX = 300;
// Mail questions keep "Checking your inbox…"; a follow-up in a thread with
// earlier replies says it's looking back, since it waits a little longer.
function pendingLabelFor(mailStatus, text, messages) {
  const label = pendingReplyLabel(mailStatus, text);
  if (label !== 'Thinking…') return label;
  const hasEarlierReplies = (messages || []).some((m) => m.role === 'assistant' && !m.error);
  return looksLikeFollowUp(text, hasEarlierReplies) ? FOLLOWUP_PENDING_LABEL : label;
}

const fromRow = (r) => withId({ role: r.role, content: r.content, replyMeta: r.reply_meta, createdAt: r.created_at });
// An older backend ignores ?limit and returns the whole list -- treat that as one complete page.
const asPage = (data) => (Array.isArray(data) ? { messages: data, start: 0 } : data);

export default function AssistantChat({ title = 'Ask Throughline', subtitle, userName, onBack, initialInput = '' }) {
  const mailStatus = useMailStatus();
  const [messages, setMessages] = useState(null); // null = loading history
  const [sending, setSending] = useState(false);
  const [pendingText, setPendingText] = useState('');
  // Pre-filled (never auto-sent) when arriving from the top search bar.
  const [input, setInput] = useState(initialInput);
  const [image, setImage] = useState(null); // { file, previewUrl }
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const [confirmQueue, setConfirmQueue] = useState([]);
  const [showJump, setShowJump] = useState(false);
  const [olderFrom, setOlderFrom] = useState(0); // server index of the oldest loaded message; 0 = all loaded
  const loadingOlder = useRef(false);
  const keepScrollFrom = useRef(null); // scrollHeight before older messages were prepended

  const scrollRef = useRef(null);
  const textareaRef = useRef(null);
  const photoInputRef = useRef(null);
  const pdfInputRef = useRef(null);
  const anyFileInputRef = useRef(null);
  const stickToBottom = useRef(true);

  // ---- history ----
  useEffect(() => {
    apiJson(`/chat/global?limit=${HISTORY_PAGE}`)
      .then((data) => {
        const page = asPage(data);
        setMessages(page.messages.map(fromRow));
        setOlderFrom(page.start);
      })
      .catch(() => setMessages([]));
  }, []);

  const loadOlder = useCallback(() => {
    if (loadingOlder.current || !olderFrom) return;
    loadingOlder.current = true;
    apiJson(`/chat/global?limit=${HISTORY_PAGE}&before=${olderFrom}`)
      .then((data) => {
        const page = asPage(data);
        keepScrollFrom.current = scrollRef.current?.scrollHeight ?? null;
        setMessages((m) => [...page.messages.map(fromRow), ...(m || [])]);
        setOlderFrom(page.start);
      })
      .catch(() => {})
      .finally(() => { loadingOlder.current = false; });
  }, [olderFrom]);

  // Prepending older messages must not move what the reader is looking at.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (keepScrollFrom.current == null || !el) return;
    el.scrollTop += el.scrollHeight - keepScrollFrom.current;
    keepScrollFrom.current = null;
  }, [messages]);

  useEffect(() => {
    if (!isPhone()) textareaRef.current?.focus();
  }, []);

  // ---- scrolling: follow new content only while the reader is at the bottom ----
  const scrollToBottom = useCallback((smooth = true) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }, []);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    stickToBottom.current = nearBottom;
    setShowJump(!nearBottom);
    if (el.scrollTop < LOAD_OLDER_PX) loadOlder();
  };

  // First paint of history jumps straight to the end; later additions glide.
  const scrolledOnce = useRef(false);
  useLayoutEffect(() => {
    if (messages === null || !stickToBottom.current) return;
    scrollToBottom(scrolledOnce.current);
    scrolledOnce.current = true;
  }, [messages, sending, scrollToBottom]);

  // ---- composer ----
  const autosize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  };
  useLayoutEffect(autosize, [input]);

  const clearImage = () => {
    if (image?.previewUrl) URL.revokeObjectURL(image.previewUrl);
    setImage(null);
  };

  const pickImage = (file) => {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { showToast('Image is too large (max 10MB)', 'error'); return; }
    clearImage();
    setImage({ file, previewUrl: URL.createObjectURL(file) });
    textareaRef.current?.focus();
  };

  const onFileChosen = (e, kind) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (kind === 'pdf' || (kind === 'any' && file.type === 'application/pdf')) sendPdf(file);
    else pickImage(file);
  };

  const openAttach = () => {
    if (isPhone()) setAttachMenuOpen(true);
    else anyFileInputRef.current?.click();
  };

  // ---- sending ----
  const append = (...items) => setMessages((m) => [...(m || []), ...items.map(withId)]);

  const queueExtractionConfirmations = (data) => {
    const entries = [];
    if (data.transaction) entries.push({ type: 'transaction', data: data.transaction, conversationId: data.conversation_id });
    if (data.policy) entries.push({ type: 'policy', data: data.policy, conversationId: data.conversation_id });
    if (entries.length) setConfirmQueue((q) => [...q, ...entries]);
  };

  const run = async (userTurn, pending, request) => {
    stickToBottom.current = true;
    if (userTurn) append(userTurn);
    setPendingText(pending);
    setSending(true);
    try {
      await request();
    } finally {
      setSending(false);
    }
  };

  // The request + reply half of a text send, shared by a fresh send and
  // Retry (whose user turn is already on screen).
  const askBackend = async (prompt) => {
    try {
      const location = await maybeGetLocation(prompt);
      const data = await post('/chat/global', { prompt, ...(location || {}) });
      const err = !data.reply && errorText(data);
      append(err
        ? { role: 'assistant', content: err, error: true, retryPrompt: prompt }
        : { role: 'assistant', content: data.reply || 'No response.', actionCards: data.action_cards || [], replyMeta: data.reply_meta });
    } catch (e) {
      append({ role: 'assistant', content: "Couldn't get an answer -- " + (e.message || 'network error') + '.', error: true, retryPrompt: prompt });
    }
  };

  const sendText = (prompt) => run({ role: 'user', content: prompt }, prompt, () => askBackend(prompt));

  const sendImage = (img, description) => run(
    { role: 'user', content: description, imageUrl: img.previewUrl }, '', async () => {
      try {
        const form = new FormData();
        form.append('image', img.file);
        form.append('description', description);
        const data = await postForm('/chat/global/image', form);
        append({ role: 'assistant', content: data.reply || errorText(data) || 'No response.', error: !data.reply });
        queueExtractionConfirmations(data);
      } catch (e) {
        append({ role: 'assistant', content: "Couldn't read that image -- " + (e.message || 'network error') + '.', error: true });
      }
    },
  );

  const sendPdf = (file) => run({ role: 'user', content: '', fileName: file.name }, '', async () => {
    try {
      const form = new FormData();
      form.append('document', file);
      const data = await postForm('/vehicle-policies/extract-pdf', form);
      append({ role: 'assistant', content: data.reply || errorText(data) || 'No response.', error: !data.reply });
      queueExtractionConfirmations(data);
    } catch (e) {
      append({ role: 'assistant', content: "Couldn't read that PDF -- " + (e.message || 'network error') + '.', error: true });
    }
  });

  const canSend = !sending && input.trim().length > 0;

  const submit = () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    if (image) {
      const img = image;
      setImage(null); // preview URL now belongs to the sent message
      sendImage(img, text);
    } else {
      sendText(text);
    }
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    } else if (e.key === 'Escape' && image) {
      clearImage();
    }
  };

  const retry = (failed) => {
    if (sending) return;
    setMessages((m) => (m || []).filter((x) => x.id !== failed.id));
    // The failed user turn is already on screen -- resend without a duplicate.
    run(null, failed.retryPrompt, () => askBackend(failed.retryPrompt));
  };
  // Stable identity so the memoized turns don't all re-render on every keystroke.
  const retryRef = useRef(retry);
  retryRef.current = retry;
  const onRetry = useCallback((failed) => retryRef.current(failed), []);

  const applyStarter = (prompt) => {
    setInput(prompt);
    textareaRef.current?.focus();
  };

  // ---- confirm sheets (photo/PDF extraction found a payment or policy) ----
  const activeConfirmation = confirmQueue[0] || null;
  const resolveConfirmation = async (edited) => {
    const entry = activeConfirmation;
    setConfirmQueue((q) => q.slice(1));
    if (!entry || !edited) return;
    try {
      if (entry.type === 'transaction') {
        const data = await post('/transactions', { ...edited, conversation_id: entry.conversationId });
        if (data.duplicate) showToast('Already saved -- looks like the same payment was shared again.', 'info');
      } else {
        const data = await post('/vehicle-policies', edited);
        showToast(data.replaced ? 'Policy updated.' : 'Policy saved.', 'success');
      }
    } catch {
      showToast(entry.type === 'transaction'
        ? 'Could not save that transaction. Try again from the chat.'
        : 'Could not save that policy. Try again from the chat.', 'error');
    }
  };

  const isEmpty = messages !== null && messages.length === 0 && !sending;
  const showMailChips = messages !== null && !sending && !hasMailReply(messages || []);
  const firstName = (userName || '').split(/\s+/)[0];

  const composer = (
    <div className="ac-composer-wrap">
      {showMailChips && !isEmpty && <MailSuggestions onPick={sendText} disabled={sending} style={{ marginBottom: 8 }} />}
      <div className={'ac-composer' + (sending ? ' is-busy' : '')}>
        {image && (
          <div className="ac-attachment">
            <img src={image.previewUrl} alt="" />
            <span>Photo attached -- say what you'd like to know about it</span>
            <button type="button" className="ac-icon-btn" onClick={clearImage} title="Remove photo" aria-label="Remove photo"><CloseIcon /></button>
          </div>
        )}
        <div className="ac-composer-row">
          <input ref={photoInputRef} type="file" accept="image/*" hidden onChange={(e) => onFileChosen(e, 'image')} />
          <input ref={pdfInputRef} type="file" accept="application/pdf" hidden onChange={(e) => onFileChosen(e, 'pdf')} />
          <input ref={anyFileInputRef} type="file" accept="image/*,application/pdf" hidden onChange={(e) => onFileChosen(e, 'any')} />
          <button
            type="button" className="ac-icon-btn ac-attach" onClick={openAttach} disabled={sending}
            title="Attach a photo or PDF" aria-label="Attach a photo or PDF"
          >
            <AttachIcon />
          </button>
          <textarea
            ref={textareaRef}
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={image ? 'What should I look for in this photo?' : 'Ask about your people, plans, email, or anything…'}
            aria-label="Message Throughline"
          />
          <button
            type="button" className="ac-send" onClick={submit} disabled={!canSend}
            title={sending ? 'Waiting for the answer…' : 'Send (Enter)'} aria-label="Send message"
          >
            <ArrowUpIcon />
          </button>
        </div>
      </div>
      <div className="ac-disclaimer">Throughline can make mistakes. Check important details.</div>
      {attachMenuOpen && (
        <AttachMenu
          onClose={() => setAttachMenuOpen(false)}
          onPickPhoto={() => photoInputRef.current?.click()}
          onPickPdf={() => pdfInputRef.current?.click()}
        />
      )}
    </div>
  );

  return (
    <div className={'ac' + (isEmpty ? ' is-empty' : '')}>
      <header className="ac-header">
        {onBack && (
          <button type="button" className="ac-icon-btn" onClick={onBack} title="Back" aria-label="Back"><BackIcon /></button>
        )}
        <div className="ac-header-text">
          <div className="ac-title">{title}</div>
          {subtitle && <div className="ac-subtitle">{subtitle}</div>}
        </div>
      </header>

      <div className="ac-scroll" ref={scrollRef} onScroll={onScroll}>
        <div className="ac-column">
          {messages === null && (
            <div className="ac-loading" aria-busy="true">
              <span className="ac-skeleton" /><span className="ac-skeleton short" /><span className="ac-skeleton" />
            </div>
          )}

          {isEmpty && (
            <div className="ac-welcome">
              <div className="ac-welcome-mark" aria-hidden="true"><SparkleIcon /></div>
              <h2>{firstName ? `What's on your mind, ${firstName}?` : "What's on your mind?"}</h2>
              <p>Ask across your recorded conversations, people, tasks and spending.</p>
              <div className="ac-starters">
                {STARTERS.map((s) => (
                  <button type="button" key={s.prompt} className="ac-starter" onClick={() => applyStarter(s.prompt)}>
                    <span className="ac-starter-title">{s.title}</span>
                    <span className="ac-starter-prompt">{s.prompt}</span>
                  </button>
                ))}
              </div>
              <MailSuggestions onPick={applyStarter} style={{ justifyContent: 'center', marginTop: 14 }} />
            </div>
          )}

          <div role="log" aria-live="polite" aria-relevant="additions">
            {olderFrom > 0 && (
              <button type="button" className="ac-load-older" onClick={loadOlder}>Show earlier messages</button>
            )}
            {(messages || []).map((m) => (m.role === 'user'
              ? <UserTurn key={m.id} message={m} />
              : <AssistantTurn key={m.id} message={m} onRetry={onRetry} />))}
          </div>

          {sending && (
            <div className="ac-turn ac-assistant" aria-live="polite">
              <div className="ac-avatar is-thinking" aria-hidden="true"><SparkleIcon /></div>
              <div className="ac-thinking">
                <span className="ac-dots" aria-hidden="true"><i /><i /><i /></span>
                {pendingLabelFor(mailStatus, pendingText, messages)}
              </div>
            </div>
          )}
        </div>
      </div>

      {showJump && !isEmpty && (
        <button type="button" className="ac-jump" onClick={() => { stickToBottom.current = true; scrollToBottom(); }} aria-label="Jump to latest">
          <ArrowDownIcon />
        </button>
      )}

      {composer}

      {activeConfirmation?.type === 'transaction' && (
        <TransactionConfirmModal
          transaction={activeConfirmation.data}
          onSave={resolveConfirmation}
          onCancel={() => resolveConfirmation(null)}
        />
      )}
      {activeConfirmation?.type === 'policy' && (
        <PolicyConfirmModal
          policy={activeConfirmation.data}
          onSave={resolveConfirmation}
          onCancel={() => resolveConfirmation(null)}
        />
      )}
    </div>
  );
}

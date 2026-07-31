import { useEffect, useState } from 'react';
import ListPane from './ListPane.jsx';
import ChatThread from './ChatThread.jsx';
import InfoDrawer from './InfoDrawer.jsx';
import CategoryMenu from './CategoryMenu.jsx';
import { useConversations } from '../hooks/useConversations.js';
import { useLiveSession } from '../hooks/useLiveSession.js';
import { post } from '../api.js';
import { backfillConversationContent } from '../db.js';
import { MicIcon } from '../icons.jsx';

export default function ChatsSection({ notify, openConversationId, onConsumeOpenConversationId }) {
  const conv = useConversations();
  const [selectedId, setSelectedId] = useState(null);
  const [selectedTranscript, setSelectedTranscript] = useState('');
  const [messages, setMessages] = useState([]);
  const [showInfo, setShowInfo] = useState(false);
  const [sending, setSending] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState('all');

  const visibleChats = categoryFilter === 'all'
    ? conv.list
    : conv.list.filter((c) => (c.category || 'personal') === categoryFilter);

  const live = useLiveSession({
    onSessionStarted: (id) => { setSelectedId(id); setMessages((m) => (selectedId === id ? m : [])); conv.refresh(); },
    onEnded: () => conv.refresh(),
  });

  const openRow = async (id) => {
    if (live.isListening) live.stop();
    setSelectedId(id);
    setShowInfo(false);
    notify?.clearChat(id);
    const { conv: c, chat } = await conv.openConversation(id);
    setSelectedTranscript(c.raw_transcript || '');
    setMessages(chat);
  };

  // Deep-link from Tasks/Profiles: "view source conversation" lands here
  // with a conversation id to open. Runs once on mount -- App remounts this
  // section fresh on every tab switch (same pattern as notify.clearTasks in
  // TasksSection), so this fires exactly when the user navigates in via a
  // deep link, and onConsumeOpenConversationId clears it in the parent so
  // a later, unrelated visit to Chats doesn't reopen the same one.
  useEffect(() => {
    if (openConversationId) {
      openRow(openConversationId);
      onConsumeOpenConversationId?.();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Below the mobile breakpoint, the list and the open chat can't share the
  // screen -- body.has-active swaps from the list to the full-screen thread
  // (see theme.css's @media block). No-op above that width, where the CSS
  // just shows both panes regardless.
  useEffect(() => {
    document.body.classList.toggle('has-active', !!selectedId);
    return () => document.body.classList.remove('has-active');
  }, [selectedId]);

  // Global "Listen" button: always visible, starts a brand-new conversation.
  const startNewChat = async () => {
    try { setSelectedTranscript(''); await live.start(); } catch (e) { alert('Could not access microphone: ' + e.message); }
  };

  // Per-conversation "Listen" button: resumes THIS conversation if it ended
  // mid-way -- new transcript lines get appended server-side rather than
  // starting an unrelated new chat.
  const resumeListening = async (id) => {
    try { await live.start(id); } catch (e) { alert('Could not access microphone: ' + e.message); }
  };

  const liveTranscript = selectedTranscript
    ? (live.transcriptText ? selectedTranscript + '\n' + live.transcriptText : selectedTranscript)
    : live.transcriptText;

  const sendMessage = async (prompt) => {
    setMessages((m) => [...m, { role: 'user', content: prompt }]);
    setSending(true);
    try {
      const transcript = live.isListening ? liveTranscript : selectedTranscript;
      const data = await post('/chat', { prompt, transcript, conversation_id: selectedId });
      const reply = data.reply || (data.error && (data.error.error || data.error)) || 'No response.';
      setMessages((m) => [...m, { role: 'assistant', content: reply }]);
      if (selectedId) backfillConversationContent(selectedId, transcript, [...messages, { content: prompt }, { content: reply }]);
    } catch (e) {
      setMessages((m) => [...m, { role: 'assistant', content: 'Request failed.' }]);
    } finally {
      setSending(false);
    }
  };

  // A tag click reuses the exact same send path as typing into the
  // composer -- the tag text becomes the chat prompt, with full context.
  const handleTagClick = (tag) => {
    sendMessage(tag);
    live.removeTag(tag);
  };

  const deleteConversation = async (id) => {
    if (!confirm('Delete this conversation permanently? This cannot be undone.')) return;
    await conv.removeConv(id);
    if (selectedId === id) { setSelectedId(null); setMessages([]); }
  };

  const selectedTitle = selectedId
    ? (conv.list.find((c) => c.id === selectedId)?.title || (live.isListening ? 'New conversation' : 'Untitled conversation'))
    : null;

  return (
    <>
      <ListPane
        title="Chats"
        headerAction={<CategoryMenu value={categoryFilter} onChange={setCategoryFilter} />}
        search={{ value: conv.query, onChange: conv.setQuery, placeholder: 'Search conversations' }}
        emptyText={categoryFilter === 'all' ? 'No conversations yet. Click Listen to start one.' : `No ${categoryFilter} conversations yet.`}
        fab={!selectedId && (
          <button
            className={'btn fab' + (live.isListening ? ' danger' : '')}
            onClick={live.isListening ? live.stop : startNewChat}
          >
            <MicIcon />
            {live.isListening ? 'Stop Listening' : 'Listen'}
          </button>
        )}
      >
        {visibleChats.map((c) => (
          <div key={c.id} className={'row' + (c.id === selectedId ? ' active' : '')} onClick={() => openRow(c.id)}>
            <span className="avatar">{(c.title || 'U')[0]}</span>
            <div className="row-main">
              <div className="row-top">
                <span className="row-title">{c.title || 'Untitled conversation'}</span>
                <span className="row-time">{new Date(c.created_at).toLocaleDateString()}</span>
              </div>
              <div className="row-sub">{new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
            </div>
            {notify?.unreadChatIds.has(c.id) && <span className="status-dot" style={{ background: '#EA0038' }} />}
          </div>
        ))}
      </ListPane>

      {selectedId ? (
        <ChatThread
          title={selectedTitle}
          isLive={live.isListening}
          liveStatus={live.status}
          seenIndices={live.seenIndices}
          speakerNames={live.speakerNames}
          pendingSpeakerIndex={live.pendingSpeakerIndex}
          knownSpeakers={live.knownSpeakers}
          onNameSpeaker={live.nameSpeaker}
          onSkipSpeaker={live.dismissPrompt}
          onReopenPrompt={live.openPrompt}
          tags={live.tags}
          onTagClick={handleTagClick}
          onDismissTag={live.removeTag}
          messages={messages}
          onSend={sendMessage}
          sending={sending}
          onToggleInfo={() => setShowInfo((v) => !v)}
          onDelete={() => deleteConversation(selectedId)}
          onListen={() => resumeListening(selectedId)}
          onStopListen={live.stop}
          onBack={() => setSelectedId(null)}
        />
      ) : (
        <div className="chat-panel empty">Select a conversation, or click Listen to start one.</div>
      )}

      {showInfo && selectedId && (
        <InfoDrawer
          title="Transcript"
          text={liveTranscript}
          onClose={() => setShowInfo(false)}
        />
      )}
    </>
  );
}

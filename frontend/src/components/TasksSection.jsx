import { useEffect, useState } from 'react';
import ListPane from './ListPane.jsx';
import CategoryMenu from './CategoryMenu.jsx';
import { MailIcon, ChatIcon } from '../icons.jsx';
import { apiJson, post } from '../api.js';

export default function TasksSection({ notify, onOpenConversation }) {
  const [status, setStatus] = useState('open');
  const [tasks, setTasks] = useState([]);
  const [categoryFilter, setCategoryFilter] = useState('all');

  const visibleTasks = categoryFilter === 'all'
    ? tasks
    : tasks.filter((t) => (t.category || 'personal') === categoryFilter);

  const load = () => apiJson('/tasks?status=' + status).then(setTasks).catch(() => {});
  useEffect(() => { load(); }, [status]); // eslint-disable-line react-hooks/exhaustive-deps
  // Viewing the tab is the "read" signal, same as opening a WhatsApp chat.
  // Runs once per mount -- App remounts this section fresh each time the
  // user switches to it, so this fires exactly on "now viewing Tasks".
  useEffect(() => { notify?.clearTasks(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = async (t) => {
    await post(`/tasks/${t.id}/${t.status === 'done' ? 'reopen' : 'complete'}`, {});
    load();
  };

  return (
    <>
      <ListPane
        title="Tasks"
        headerAction={
          <>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="open">Open</option>
              <option value="done">Done</option>
              <option value="all">All</option>
            </select>
            <CategoryMenu value={categoryFilter} onChange={setCategoryFilter} />
          </>
        }
        emptyText={categoryFilter === 'all' ? 'No tasks here yet.' : `No ${categoryFilter} tasks yet.`}
      >
        {visibleTasks.map((t) => (
          <div key={t.id} className="row" onClick={() => toggle(t)}>
            <span className="avatar" style={{ background: t.status === 'done' ? '#8696A0' : undefined }}>
              {t.status === 'done' ? '✓' : '○'}
            </span>
            <div className="row-main">
              <div className="row-top">
                <span className="row-title">{t.description}</span>
                <span className="row-top-icons">
                  {t.email_sent && (
                    <span className="email-sent-badge" title="Reminder email sent">
                      <MailIcon />
                    </span>
                  )}
                  {t.conversation_id && (
                    <button
                      className="conv-link-btn"
                      title="View source conversation"
                      onClick={(e) => { e.stopPropagation(); onOpenConversation?.(t.conversation_id); }}
                    >
                      <ChatIcon />
                    </button>
                  )}
                </span>
              </div>
              <div className="row-sub">
                {[t.owner && `Owner: ${t.owner}`, t.due_date && `Due: ${t.due_date}`].filter(Boolean).join(' · ') || 'No details'}
              </div>
            </div>
          </div>
        ))}
      </ListPane>
      <div className="chat-panel empty">Click a task to mark it done or reopen it.</div>
    </>
  );
}

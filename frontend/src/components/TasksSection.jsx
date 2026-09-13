import { useEffect, useState } from 'react';
import ListPane from './ListPane.jsx';
import CategoryMenu from './CategoryMenu.jsx';
import ContextMenu from './ContextMenu.jsx';
import { MailIcon, ChatIcon, PencilIcon, TrashIcon, BackIcon, CheckIcon } from '../icons.jsx';
import { apiJson, post, del } from '../api.js';
import { confirmDialog } from '../lib/notify.js';
import { onEnterOrSpace } from '../lib/a11y.js';
import { useLongPress } from '../hooks/useLongPress.js';
import UseAnimations from 'react-useanimations';
import checkBox from 'react-useanimations/lib/checkBox';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString([], { year: 'numeric', month: 'long', day: 'numeric' });
}

export default function TasksSection({ notify, onOpenConversation }) {
  const [status, setStatus] = useState('open');
  const [tasks, setTasks] = useState([]);
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [selectedId, setSelectedId] = useState(null);
  const [editing, setEditing] = useState(false);
  const [editDraft, setEditDraft] = useState({ description: '', due_date: '' });
  const [rowMenu, setRowMenu] = useState(null); // {x, y, task} for the long-press quick-action menu
  const bindLongPress = useLongPress();

  const visibleTasks = categoryFilter === 'all'
    ? tasks
    : tasks.filter((t) => (t.category || 'personal') === categoryFilter);

  // The task shown in the detail pane -- looked up fresh from `tasks` on
  // every render (not held as its own copy) so toggling/editing it updates
  // the detail pane the instant `load()` refetches, same as the list row.
  const selected = tasks.find((t) => t.id === selectedId) || null;

  const load = () => apiJson('/tasks?status=' + status).then(setTasks).catch(() => {});
  useEffect(() => { load(); }, [status]); // eslint-disable-line react-hooks/exhaustive-deps
  // Viewing the tab is the "read" signal, same as opening a WhatsApp chat.
  // Runs once per mount -- App remounts this section fresh each time the
  // user switches to it, so this fires exactly on "now viewing Tasks".
  useEffect(() => { notify?.clearTasks(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Covers the one gap the mount-time load above doesn't: a task_created
  // push (e.g. a reminder detected in chat) arriving while already sitting
  // on this tab, which wouldn't otherwise trigger a remount.
  useEffect(() => {
    if (notify?.taskCount > 0) {
      notify.clearTasks();
      load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notify?.taskCount]);

  // See ChatsSection/ProfilesSection/FriendsSection's identical effect --
  // swaps list/detail on mobile widths.
  useEffect(() => {
    document.body.classList.toggle('has-active', !!selectedId);
    return () => document.body.classList.remove('has-active');
  }, [selectedId]);

  // Status changes on a task currently open in the detail pane keep it
  // selected (selected is derived from `tasks`, not copied), but the `all`
  // filter is the only status view where a done/reopened task doesn't drop
  // out of the visible list entirely -- worth knowing if you're staring at
  // detail for a task that just vanished from an `open`/`done`-filtered list.
  const openTask = (t) => {
    setSelectedId(t.id);
    setEditing(false);
  };

  const toggle = async (t, e) => {
    e?.stopPropagation();
    await post(`/tasks/${t.id}/${t.status === 'done' ? 'reopen' : 'complete'}`, {});
    load();
  };

  const startEdit = () => {
    setEditDraft({ description: selected.description, due_date: selected.due_date || '' });
    setEditing(true);
  };

  const saveEdit = async () => {
    if (!editDraft.description.trim()) return;
    await post(`/tasks/${selected.id}/edit`, editDraft);
    setEditing(false);
    load();
  };

  const deleteTask = async (task = selected) => {
    const ok = await confirmDialog({
      title: 'Delete this task?',
      message: 'This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
    });
    if (!ok) return;
    await del(`/tasks/${task.id}`);
    if (task.id === selectedId) setSelectedId(null);
    load();
  };

  return (
    <>
      <ListPane
        title="Tasks"
        headerAction={
          <>
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
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
          <div
            key={t.id} className={'row' + (t.id === selectedId ? ' active' : '')}
            role="button" tabIndex={0} aria-label={`Task: ${t.description}`}
            onClick={() => openTask(t)} onKeyDown={onEnterOrSpace(() => openTask(t))}
            {...bindLongPress((x, y) => setRowMenu({ x, y, task: t }))}
          >
            <button
              className="avatar" style={{ background: t.status === 'done' ? 'var(--wa-text-soft)' : undefined }}
              title={t.status === 'done' ? 'Reopen' : 'Mark done'} aria-label={t.status === 'done' ? 'Reopen task' : 'Mark task done'}
              onClick={(e) => toggle(t, e)}
            >
              {t.status === 'done' ? '✓' : '○'}
            </button>
            <div className="row-main">
              <div className="row-top">
                <span className="row-title">{t.description}</span>
                {t.email_sent && (
                  <span className="email-sent-badge" title="Reminder email sent">
                    <MailIcon />
                  </span>
                )}
              </div>
              <div className="row-sub">
                {[t.owner && `Owner: ${t.owner}`, t.due_date && `Due: ${t.due_date}`].filter(Boolean).join(' · ') || 'No details'}
              </div>
            </div>
          </div>
        ))}
      </ListPane>

      {rowMenu && (
        <ContextMenu
          x={rowMenu.x} y={rowMenu.y} onClose={() => setRowMenu(null)}
          items={[
            {
              label: rowMenu.task.status === 'done' ? 'Reopen' : 'Mark done', icon: <CheckIcon />,
              onSelect: () => toggle(rowMenu.task),
            },
            { label: 'Delete task', icon: <TrashIcon />, danger: true, onSelect: () => deleteTask(rowMenu.task) },
          ]}
        />
      )}

      {selected ? (
        <div className="detail-pane detail-view" style={{ flex: 1 }}>
          <div className="detail-card">
            <button className="back-btn" title="Back" aria-label="Back to task list" onClick={() => setSelectedId(null)}><BackIcon /></button>

            <div className="detail-title-row">
              <span className="detail-meta" style={{ display: 'flex', alignItems: 'center', margin: 0 }}>
                <span className="status-dot" style={{ background: selected.status === 'done' ? 'var(--wa-accent)' : 'var(--wa-text-soft)' }} />
                {selected.status === 'done' ? 'Done' : 'Open'}
              </span>
              {!editing && (
                <button className="conv-link-btn" title="Edit task" aria-label="Edit task" onClick={startEdit}><PencilIcon /></button>
              )}
            </div>

            {editing ? (
              <>
                <input
                  className="field" value={editDraft.description} autoFocus
                  onChange={(e) => setEditDraft((d) => ({ ...d, description: e.target.value }))}
                  placeholder="Description" aria-label="Task description"
                />
                <input
                  className="field" value={editDraft.due_date}
                  onChange={(e) => setEditDraft((d) => ({ ...d, due_date: e.target.value }))}
                  placeholder="Due date (e.g. Friday)" aria-label="Due date"
                />
                <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                  <button className="btn" onClick={saveEdit}>Save</button>
                  <button className="btn secondary" onClick={() => setEditing(false)}>Cancel</button>
                </div>
              </>
            ) : (
              <p style={{ fontSize: 15.5, lineHeight: 1.5, margin: '4px 0 0' }}>{selected.description}</p>
            )}

            <div className="profile-section">
              <div className="profile-section-title">Details</div>
              <div className="detail-meta-row">
                <span className="hint" style={{ margin: 0 }}>Owner</span>
                <span className="detail-meta" style={{ margin: 0 }}>{selected.owner || '—'}</span>
              </div>
              <div className="detail-meta-row">
                <span className="hint" style={{ margin: 0 }}>Due</span>
                <span className="detail-meta" style={{ margin: 0 }}>{selected.due_date || '—'}</span>
              </div>
              <div className="detail-meta-row">
                <span className="hint" style={{ margin: 0 }}>Category</span>
                <span className="detail-meta" style={{ margin: 0, textTransform: 'capitalize' }}>{selected.category || 'personal'}</span>
              </div>
              <div className="detail-meta-row">
                <span className="hint" style={{ margin: 0 }}>Reminder email</span>
                <span className="detail-meta" style={{ margin: 0 }}>{selected.email_sent ? 'Sent' : 'Not sent'}</span>
              </div>
              <div className="detail-meta-row">
                <span className="hint" style={{ margin: 0 }}>Created</span>
                <span className="detail-meta" style={{ margin: 0 }}>{formatDate(selected.created_at)}</span>
              </div>
            </div>

            <div className="profile-section" style={{ display: 'flex', gap: 8 }}>
              <button className="btn" style={{ flex: 1 }} onClick={(e) => toggle(selected, e)}>
                {selected.status === 'done' ? 'Reopen' : 'Mark done'}
              </button>
              {selected.conversation_id && (
                <button
                  className="btn secondary" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
                  onClick={() => onOpenConversation?.(selected.conversation_id)}
                >
                  <ChatIcon /> Source
                </button>
              )}
              <button
                className="btn danger" style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}
                onClick={deleteTask}
              >
                <TrashIcon /> Delete
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="chat-panel empty">
          <div className="empty-state">
            <UseAnimations animation={checkBox} size={110} autoplay loop strokeColor="#1FC8B4" />
            <div className="empty-state-text">Click a task to see its details.</div>
          </div>
        </div>
      )}
    </>
  );
}

import { useEffect, useRef, useState } from 'react';
import { apiJson, post } from '../api.js';
import {
  SunIcon, CloudIcon, BoltIcon, ChevronRightIcon, BellIcon, SparkleIcon, PlusIcon,
  ChatIcon, MicIcon, TaskIcon, FriendIcon, AttachIcon, ProfileIcon, SendIcon,
} from '../icons.jsx';

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Good Morning';
  if (h < 17) return 'Good Afternoon';
  return 'Good Evening';
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function dateString(now) {
  return `${WEEKDAYS[now.getDay() === 0 ? 6 : now.getDay() - 1]}, ${now.getDate()} ${MONTHS[now.getMonth()]} ${now.getFullYear()}`;
}

function relativeTime(iso) {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 60) return `${Math.max(0, mins)}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 2) return 'Yesterday';
  if (days < 7) return `${days}d ago`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

// Reusable small section label -- matches the Flutter Home screen's
// _SectionHeader (title + optional icon + optional "See all ->" link).
function SectionHeader({ title, icon, onSeeAll }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      {icon}
      <span style={{ flex: 1, fontSize: 14, fontWeight: 700 }}>{title}</span>
      {onSeeAll && (
        <button type="button" onClick={onSeeAll} style={{ display: 'flex', alignItems: 'center', gap: 2, fontSize: 12, color: 'var(--wa-text-soft)' }}>
          See all <ChevronRightIcon style={{ width: 12, height: 12 }} />
        </button>
      )}
    </div>
  );
}

// Web counterpart of the Flutter client's HomeScreen (screens/home/home_screen.dart)
// -- every component on that screen, component-for-component: top greeting,
// the "for you today" insight, a Your World teaser, Quick Actions, Reminders
// + Recent Activity, a rotating Weekly Insight, and the bottom Ask bar.
// Entrance/micro-animations (staggered fade-slide-in, the rainbow sweep, the
// pulsing "new" halo, per-tile tap-bounce, the drawn checkmark) aren't
// reproduced -- everything that's actually DATA and layout is.
export default function HomeSection({ username, onOpenConversation, onOpenGlobalChat, onNavigate }) {
  const [digest, setDigest] = useState(null); // null = loading, else array of {category,label,headline,body}
  const [openTasks, setOpenTasks] = useState(null);
  const [conversations, setConversations] = useState(null);
  const [friends, setFriends] = useState(null);
  const now = useRef(new Date()).current;

  useEffect(() => {
    // /insights/digest's real shape is {digest: {cards: [...]}} (or
    // {digest: null} when none exists yet) -- NOT a bare array. Confirmed
    // live: treating d.digest itself as the array (as OsApp.jsx's older
    // Home/Insights sections already did, silently wrong there too) throws
    // "digest.filter is not a function" the moment anything here calls an
    // array method on it, which crashed this whole screen.
    apiJson('/insights/digest').then((d) => setDigest((d.digest && d.digest.cards) || [])).catch(() => setDigest([]));
    apiJson('/tasks?status=open').then(setOpenTasks).catch(() => setOpenTasks([]));
    apiJson('/conversations').then(setConversations).catch(() => setConversations([]));
    apiJson('/friends').then(setFriends).catch(() => setFriends([]));
  }, []);

  const loaded = openTasks !== null && conversations !== null;
  const firstCard = digest && digest.length ? digest[0] : null;
  const relationshipCards = (digest || []).filter((c) => c.category === 'relationship');
  const weeklyInsights = (digest || [])
    .map((c) => (c.body || '').trim() || (c.headline || '').trim())
    .filter(Boolean);

  const completeTask = async (task) => {
    setOpenTasks((ts) => ts.filter((t) => t.id !== task.id));
    try { await post(`/tasks/${task.id}/complete`, {}); } catch (e) { /* best-effort, matches the Flutter screen's own fire-and-forget complete */ }
  };

  return (
    <div className="home-section">
      <GreetingHeader username={username} now={now} />

      <InsightCard
        headline={firstCard?.headline || "Keep talking -- your first insight appears once there's enough to look at."}
        body={firstCard?.body}
        onViewSummary={firstCard ? onOpenGlobalChat : undefined}
      />

      <WorldTeaserCard friends={friends} relationshipCards={relationshipCards} onTap={() => onNavigate('/app/friends')} />

      <div style={{ marginTop: 20 }}>
        <SectionHeader title="Quick Actions" />
      </div>
      <QuickActionsRow
        onChat={onOpenGlobalChat}
        onListen={() => onNavigate('/app/chats')}
        onTasks={() => onNavigate('/app/tasks')}
        onPeople={() => onNavigate('/app/friends')}
        onAttach={onOpenGlobalChat}
      />

      <div className="home-pair-row">
        <RemindersCard loaded={loaded} tasks={openTasks || []} onSeeAll={() => onNavigate('/app/tasks')} onComplete={completeTask} />
        <RecentActivityCard loaded={loaded} conversations={conversations || []} onOpen={onOpenConversation} />
      </div>

      <WeeklyInsightCard insights={weeklyInsights} />

      <AskBar onTap={onOpenGlobalChat} />
    </div>
  );
}

function GreetingHeader({ username, now }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
      <SunIcon style={{ color: 'var(--wa-text-soft)' }} />
      <div style={{ flex: 1 }}>
        <div className="home-greeting-title">{greeting()}{username ? `, ${username}` : ''}</div>
        <div className="hint" style={{ margin: 0 }}>Here's what's happening with your world today.</div>
      </div>
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        <div style={{ fontSize: 11.5, color: 'var(--wa-text-soft)' }}>{dateString(now)}</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 3, justifyContent: 'flex-end', marginTop: 4, fontSize: 11.5, color: 'var(--wa-text-soft)' }}>
          <CloudIcon style={{ width: 14, height: 14 }} /> --&deg; &middot; --
        </div>
      </div>
    </div>
  );
}

function InsightCard({ headline, body, onViewSummary }) {
  return (
    <div className="home-card home-insight-card">
      <span className="home-insight-badge"><BoltIcon style={{ width: 12, height: 12 }} /> FOR YOU TODAY</span>
      <div className="home-insight-headline home-rainbow-text">{headline}</div>
      {body && <div className="home-insight-body">{body}</div>}
      {onViewSummary && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 10 }}>
          <button type="button" className="btn secondary small" onClick={onViewSummary}>
            View Summary <ChevronRightIcon style={{ width: 13, height: 13 }} />
          </button>
        </div>
      )}
    </div>
  );
}

function WorldTeaserCard({ friends, relationshipCards, onTap }) {
  const list = friends || [];
  const shown = list.slice(0, 4);
  const extra = list.length - shown.length;

  let subtitle;
  if (relationshipCards.length) {
    const card = relationshipCards[0];
    subtitle = (card.body || '').trim() || (card.headline || '').trim();
  } else if (list.length === 0) {
    subtitle = friends === null ? 'Loading...' : 'Add a friend to start your world';
  } else {
    const mostRecent = list
      .filter((f) => f.last_call_at)
      .sort((a, b) => new Date(b.last_call_at) - new Date(a.last_call_at))[0];
    subtitle = mostRecent
      ? `Last thread with ${mostRecent.nickname || mostRecent.username} • ${relativeTime(mostRecent.last_call_at)}`
      : 'Tap to see your relationship thread';
  }

  return (
    <button type="button" className="home-card home-world-teaser" onClick={onTap}>
      {shown.length > 0 && (
        <div className="home-world-avatars" style={{ width: 16 * (shown.length - 1) + 32 + (extra > 0 ? 16 : 0) }}>
          {shown.map((f, i) => (
            <span key={f.id} className="home-world-avatar" style={{ left: i * 16 }}><ProfileIcon /></span>
          ))}
          {extra > 0 && <span className="home-world-avatar home-world-avatar-extra" style={{ left: shown.length * 16 }}>+{extra}</span>}
        </div>
      )}
      <div style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
        <div style={{ fontSize: 13.5, fontWeight: 700 }}>Your World</div>
        <div className="row-sub" style={{ marginTop: 2 }}>{subtitle}</div>
      </div>
      <ChevronRightIcon style={{ color: 'var(--wa-text-soft)' }} />
    </button>
  );
}

const QUICK_ACTIONS = [
  { Icon: ChatIcon, label: 'Chat', sub: 'Talk to your AI', key: 'onChat' },
  { Icon: MicIcon, label: 'Listen', sub: 'Voice notes & calls', key: 'onListen' },
  { Icon: TaskIcon, label: 'Tasks', sub: 'Manage your tasks', key: 'onTasks' },
  { Icon: FriendIcon, label: 'People', sub: 'Your network', key: 'onPeople' },
  { Icon: AttachIcon, label: 'Add', sub: 'Bills, docs, images', key: 'onAttach' },
];

function QuickActionsRow(handlers) {
  return (
    <div className="home-quick-actions">
      {QUICK_ACTIONS.map(({ Icon, label, sub, key }) => (
        <button type="button" key={key} className="home-quick-tile" onClick={handlers[key]}>
          <span className="home-quick-tile-icon"><Icon /></span>
          <span className="home-quick-tile-label">{label}</span>
          <span className="home-quick-tile-sub">{sub}</span>
        </button>
      ))}
    </div>
  );
}

function RemindersCard({ loaded, tasks, onSeeAll, onComplete }) {
  const shown = tasks.slice(0, 4);
  return (
    <div className="home-card" style={{ flex: 1, marginTop: 0 }}>
      <SectionHeader title="Reminders" icon={<BellIcon style={{ width: 16, height: 16, color: 'var(--wa-text-soft)' }} />} onSeeAll={onSeeAll} />
      <div style={{ marginTop: 10 }}>
        {!loaded ? (
          <div className="hint" style={{ margin: 0 }}>Loading...</div>
        ) : shown.length === 0 ? (
          <div className="hint" style={{ margin: 0 }}>No open tasks.</div>
        ) : (
          shown.map((t) => (
            <label key={t.id} className="home-reminder-row">
              <input type="checkbox" onChange={() => onComplete(t)} />
              <span>
                <span className="home-reminder-desc">{t.description}</span>
                {t.due_date && <span className="home-reminder-due">{t.due_date}</span>}
              </span>
            </label>
          ))
        )}
      </div>
    </div>
  );
}

function RecentActivityCard({ loaded, conversations, onOpen }) {
  const shown = conversations.slice(0, 4);
  return (
    <div className="home-card" style={{ flex: 1, marginTop: 0 }}>
      <SectionHeader title="Recent Activity" icon={<ChatIcon style={{ width: 16, height: 16, color: 'var(--wa-text-soft)' }} />} />
      <div style={{ marginTop: 10 }}>
        {!loaded ? (
          <div className="hint" style={{ margin: 0 }}>Loading...</div>
        ) : shown.length === 0 ? (
          <div className="hint" style={{ margin: 0 }}>Nothing recorded yet.</div>
        ) : (
          shown.map((c) => (
            <button type="button" key={c.id} className="home-activity-row" onClick={() => onOpen(c.id)}>
              <ChatIcon style={{ width: 14, height: 14, color: 'var(--wa-text-soft)', flexShrink: 0 }} />
              <span style={{ flex: 1, minWidth: 0, textAlign: 'left' }}>
                <span className="home-activity-title">{c.title || 'Untitled conversation'}</span>
                <span className="home-activity-meta">{relativeTime(c.created_at)} &middot; {c.category || 'personal'}</span>
              </span>
              <ChevronRightIcon style={{ width: 16, height: 16, color: 'var(--wa-text-soft)', flexShrink: 0 }} />
            </button>
          ))
        )}
      </div>
    </div>
  );
}

// Cycles through this week's digest cards every 5s -- deliberately
// understated (small, muted) since InsightCard above already surfaces the
// single most notable one.
function WeeklyInsightCard({ insights }) {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    if (insights.length < 2) return;
    const id = setInterval(() => setIndex((i) => (i + 1) % insights.length), 5000);
    return () => clearInterval(id);
  }, [insights.length]);

  return (
    <div className="home-card">
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <SparkleIcon style={{ width: 14, height: 14, color: 'var(--wa-text-soft)' }} />
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4, color: 'var(--wa-text-soft)' }}>WEEKLY INSIGHT</span>
      </div>
      <div className="hint" style={{ margin: '8px 0 0' }}>
        {insights.length ? insights[index % insights.length] : "Keep talking -- weekly insights show up here once there's enough to look at."}
      </div>
    </div>
  );
}

function AskBar({ onTap }) {
  return (
    <div className="home-ask-bar" onClick={onTap} role="button" tabIndex={0}>
      <span className="home-ask-bar-icon"><PlusIcon /></span>
      <span className="home-ask-bar-text">Ask about your people, plans, or anything...</span>
      <span className="home-ask-bar-send"><SendIcon /></span>
    </div>
  );
}

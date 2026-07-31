import { useEffect, useState } from 'react';
import { useAuth } from './hooks/useAuth.js';
import { useNotifications } from './hooks/useNotifications.js';
import { apiJson } from './api.js';
import AuthScreen from './components/AuthScreen.jsx';
import PersonaOnboarding from './components/PersonaOnboarding.jsx';
import IconRail from './components/IconRail.jsx';
import ChatsSection from './components/ChatsSection.jsx';
import TasksSection from './components/TasksSection.jsx';
import ProfilesSection from './components/ProfilesSection.jsx';
import FriendsSection from './components/FriendsSection.jsx';
import SettingsSection from './components/SettingsSection.jsx';

const SECTIONS = {
  chats: ChatsSection,
  tasks: TasksSection,
  profiles: ProfilesSection,
  friends: FriendsSection,
};

export default function App() {
  const auth = useAuth();
  const [section, setSection] = useState('chats');
  // Owned here (not per-section) so the socket survives switching tabs --
  // a badge from a section you're not currently viewing should still count.
  const notify = useNotifications(!!auth.user);
  // Set by Tasks/Profiles "view source conversation" links, consumed once
  // by ChatsSection then cleared -- see its openConversationId effect.
  const [pendingConversationId, setPendingConversationId] = useState(null);
  // null = not checked yet, true/false once checked. Gates the app shell
  // behind a one-time persona form for any account that hasn't submitted
  // one -- covers brand-new signups and pre-existing accounts alike.
  const [personaCompleted, setPersonaCompleted] = useState(null);

  useEffect(() => {
    if (!auth.user) return;
    apiJson('/persona')
      .then((d) => setPersonaCompleted(d.completed))
      .catch(() => setPersonaCompleted(true)); // fail-open -- don't block the app on a network hiccup
  }, [auth.user]);

  if (auth.loading) return null;
  if (!auth.user) return <AuthScreen auth={auth} />;
  if (personaCompleted === null) return null;
  if (!personaCompleted) return <PersonaOnboarding onComplete={() => setPersonaCompleted(true)} />;

  const Section = SECTIONS[section];

  const openConversationInChats = (conversationId) => {
    if (!conversationId) return;
    setPendingConversationId(conversationId);
    setSection('chats');
  };

  return (
    <div className="app-shell">
      <IconRail
        active={section}
        onSelect={setSection}
        username={auth.user.username}
        badges={{ tasks: notify.taskCount, chats: notify.unreadChatIds.size }}
      />
      {section === 'settings' && <SettingsSection user={auth.user} onLogout={auth.logout} />}
      {section === 'chats' && (
        <ChatsSection
          notify={notify}
          openConversationId={pendingConversationId}
          onConsumeOpenConversationId={() => setPendingConversationId(null)}
        />
      )}
      {section === 'tasks' && <TasksSection notify={notify} onOpenConversation={openConversationInChats} />}
      {section === 'profiles' && <ProfilesSection onOpenConversation={openConversationInChats} />}
      {section === 'friends' && <FriendsSection />}
    </div>
  );
}

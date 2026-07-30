import { useState } from 'react';
import { useAuth } from './hooks/useAuth.js';
import { useNotifications } from './hooks/useNotifications.js';
import AuthScreen from './components/AuthScreen.jsx';
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

  if (auth.loading) return null;
  if (!auth.user) return <AuthScreen auth={auth} />;

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

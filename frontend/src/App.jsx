import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from './hooks/useAuth.js';
import { useNotifications } from './hooks/useNotifications.js';
import { useCall } from './hooks/useCall.js';
import { useTheme } from './hooks/useTheme.js';
import { apiJson } from './api.js';
import { showToast } from './lib/notify.js';
import AuthScreen from './components/AuthScreen.jsx';
import PersonaOnboarding from './components/PersonaOnboarding.jsx';
import CallOverlay from './components/CallOverlay.jsx';
import NotificationHost from './components/NotificationHost.jsx';
import { startRingtone, stopRingtone, unlockAudio } from './ringtone.js';
import IconRail from './components/IconRail.jsx';

/// Root layout route (see router.jsx) -- rendered once and never unmounted
/// by navigation between sections, which is exactly what auth/notify/call
/// need: notify (the /ws/notify socket) and call (an active LiveKit call)
/// must both survive switching tabs, same guarantee this component gave
/// when section-switching was a plain useState instead of a route change.
/// Section components themselves are unchanged (see router.jsx's per-route
/// wrappers) -- only how "which section is active" is derived changed, from
/// local state to the URL.
export default function App() {
  const auth = useAuth();
  const { theme, toggleTheme } = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  // Owned here (not per-section) so the socket survives switching tabs --
  // a badge from a section you're not currently viewing should still count.
  const notify = useNotifications(!!auth.user);
  // Owned here too, same reasoning as notify -- an active/incoming call
  // must stay visible (and connected) across tab switches.
  const call = useCall();
  // Set by Tasks/Profiles "view source conversation" links, consumed once
  // by ChatsSection then cleared -- see openConversationInChats below.
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

  // Rings while a call is coming in and hasn't been picked up/joined yet --
  // stops the moment it's accepted, declined, or cleared for any reason.
  useEffect(() => {
    if (notify.incomingCall && !call.activeCall) startRingtone();
    else stopRingtone();
    return () => stopRingtone();
  }, [notify.incomingCall, call.activeCall]);

  // The initiator's own room connects immediately on startCall (before
  // anyone accepts), so if every invitee declines, LiveKit never fires a
  // disconnect for them -- the server-pushed call_declined event is the
  // only signal telling this side to hang up instead of lingering.
  useEffect(() => {
    if (!notify.declinedCallId) return;
    notify.clearDeclinedCall();
    if (call.activeCall?.callId === notify.declinedCallId) call.leaveCall();
  }, [notify.declinedCallId]);

  // iOS only allows audio playback (the ringtone) if the AudioContext was
  // primed during a real tap -- an incoming call itself has no preceding
  // gesture to piggyback on, so this grabs the very first tap/click
  // anywhere in the app (login, switching tabs, whatever happens first)
  // to prime it ahead of time. Harmless no-op on browsers that don't need it.
  useEffect(() => {
    const unlock = () => unlockAudio();
    document.addEventListener('click', unlock, { once: true });
    document.addEventListener('touchstart', unlock, { once: true });
    return () => {
      document.removeEventListener('click', unlock);
      document.removeEventListener('touchstart', unlock);
    };
  }, []);

  if (auth.loading) return null;
  if (!auth.user) return <AuthScreen auth={auth} />;
  if (personaCompleted === null) return null;
  if (!personaCompleted) return <PersonaOnboarding onComplete={() => setPersonaCompleted(true)} />;

  const openConversationInChats = (conversationId) => {
    if (!conversationId) return;
    setPendingConversationId(conversationId);
    navigate('/app/chats');
  };

  const acceptIncomingCall = async () => {
    const incoming = notify.incomingCall;
    notify.clearIncomingCall();
    try { await call.joinCall(incoming.callId); } catch (e) { showToast('Could not join call: ' + e.message, 'error'); }
  };

  const declineIncomingCall = () => {
    const incoming = notify.incomingCall;
    notify.clearIncomingCall();
    call.declineCall(incoming.callId).catch(() => {});
  };

  // Derived from the URL instead of local state -- IconRail's own props
  // (active/onSelect) are otherwise completely unchanged. Strips the /app
  // prefix (see router.jsx for why every client route lives under it).
  const section = location.pathname.replace(/^\/app\/?/, '').split('/')[0] || 'os';

  return (
    <div className="app-shell">
      {/* The "os" screen renders its own full sidebar (see os/OsApp.jsx) --
          showing IconRail alongside it would double up navigation. */}
      {section !== 'os' && (
        <IconRail
          active={section}
          onSelect={(s) => navigate('/app/' + s)}
          username={auth.user.username}
          profilePictureUrl={auth.user.profile_picture_url}
          badges={{ tasks: notify.taskCount, chats: notify.unreadChatIds.size }}
          online={notify.connected}
        />
      )}
      <Outlet
        context={{
          auth, notify, call, theme, toggleTheme,
          pendingConversationId,
          openConversationInChats,
          consumePendingConversationId: () => setPendingConversationId(null),
        }}
      />
      <CallOverlay
        incomingCall={call.activeCall ? null : notify.incomingCall}
        activeCall={call.activeCall}
        onAccept={acceptIncomingCall}
        onDecline={declineIncomingCall}
        onLeave={call.leaveCall}
        onToggleMute={call.toggleMute}
        onEnableAudio={call.enableAudio}
      />
      <NotificationHost />
    </div>
  );
}

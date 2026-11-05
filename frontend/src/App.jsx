import { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from './hooks/useAuth.js';
import { useNotifications } from './hooks/useNotifications.js';
import { useCall } from './hooks/useCall.js';
import { useTheme } from './hooks/useTheme.js';
import { useIsPhone } from './hooks/useIsPhone.js';
import { apiJson } from './api.js';
import { showToast } from './lib/notify.js';
import { refreshMailStatus, MAIL_PROVIDERS, PROVIDER_KEYS } from './lib/mail.js';
import AuthScreen from './components/AuthScreen.jsx';
import PersonaOnboarding from './components/PersonaOnboarding.jsx';
import CallOverlay from './components/CallOverlay.jsx';
import NotificationHost from './components/NotificationHost.jsx';
import AppDrawer from './components/AppDrawer.jsx';
import { startRingtone, stopRingtone, unlockAudio } from './ringtone.js';
import IconRail from './components/IconRail.jsx';
import { MenuIcon } from './icons.jsx';
import { SECTION_TITLES } from './lib/nav.js';
import { ensureRecordingNotice, resetConsentCache } from './lib/consent.js';
import ConsentHost from './components/ConsentHost.jsx';

// "Skip for now" on the onboarding questions is remembered per account on
// this device, so it isn't shown again on every visit -- the Home screens
// keep a small "Personalize" card until it's done.
const personaSkipKey = (userId) => `tl_persona_skipped_${userId}`;
function personaSkipped(userId) {
  try { return localStorage.getItem(personaSkipKey(userId)) === '1'; } catch { return false; }
}

// Shown while the very first sign-in check runs (no cached user yet) --
// the shape of the app instead of a blank page.
function LoadingShell() {
  return (
    <div className="app-loading" role="status" aria-live="polite">
      <div className="app-loading-rail" />
      <div className="app-loading-main">
        <div className="app-loading-bar" style={{ width: '38%' }} />
        <div className="app-loading-bar" style={{ width: '62%' }} />
        <div className="app-loading-block" />
        <span className="sr-only">Loading Throughline…</span>
      </div>
    </div>
  );
}

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
  const isPhone = useIsPhone();
  const [drawerOpen, setDrawerOpen] = useState(false);
  // Owned here (not per-section) so the socket survives switching tabs --
  // a badge from a section you're not currently viewing should still count.
  const notify = useNotifications(!!auth.user);
  // Owned here too, same reasoning as notify -- an active/incoming call
  // must stay visible (and connected) across tab switches.
  const call = useCall();
  // Set by Tasks/Profiles "view source conversation" links, consumed once
  // by ChatsSection then cleared -- see openConversationInChats below.
  const [pendingConversationId, setPendingConversationId] = useState(null);
  // Set by Home's Ask bar/Quick Actions (see HomeSection.jsx), consumed once
  // by ChatsSection to open its global "Ask" thread immediately on arrival --
  // same open-once-then-clear shape as pendingConversationId above, mirrors
  // the Flutter Home screen's _openChat() opening the chat in place.
  const [pendingOpenGlobalChat, setPendingOpenGlobalChat] = useState(false);
  // null = not checked yet, true/false once checked. The persona questions
  // no longer block the app: they open once for an account that hasn't
  // answered them, with "Skip for now", and Home keeps a reminder card.
  const [personaCompleted, setPersonaCompleted] = useState(null);
  const [personaOpen, setPersonaOpen] = useState(false);
  const userId = auth.user?.id;

  // Consent answers are per account -- never reuse another user's.
  useEffect(() => { resetConsentCache(); }, [userId]);

  useEffect(() => {
    if (!userId) return;
    apiJson('/persona')
      .then((d) => {
        setPersonaCompleted(d.completed);
        if (!d.completed && !personaSkipped(userId)) setPersonaOpen(true);
      })
      .catch(() => setPersonaCompleted(true)); // fail-open -- don't nag on a network hiccup
  }, [userId]);

  const skipPersona = () => {
    try { localStorage.setItem(personaSkipKey(userId), '1'); } catch { /* ignore */ }
    setPersonaOpen(false);
  };

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

  // Back from a mailbox OAuth round trip (the backend's callback redirects
  // to /app?outlook=connected|error or /app?gmail=...) -- confirm the result
  // right where the user lands, then drop the params so a reload doesn't
  // repeat it.
  useEffect(() => {
    if (!auth.user) return;
    const params = new URLSearchParams(location.search);
    const provider = PROVIDER_KEYS.find((p) => params.get(p));
    if (!provider) return;
    const label = MAIL_PROVIDERS[provider].label;
    if (params.get(provider) === 'connected') {
      const who = params.get(`${provider}_email`);
      showToast(
        `${label} connected${who ? ' as ' + who : ''}. Try asking: “Summarize my unread emails”.`,
        'success', 8000,
      );
    } else {
      showToast(`Couldn’t connect ${label}: ` + (params.get(`${provider}_error`) || 'please try again.'), 'error', 9000);
    }
    refreshMailStatus();
    [provider, `${provider}_email`, `${provider}_error`].forEach((k) => params.delete(k));
    const rest = params.toString();
    // window.location, not `location`: /app's DefaultRedirect (a child, so
    // its effect runs first) may already have moved us to /app/os or
    // /app/home in this same commit.
    navigate(window.location.pathname + (rest ? '?' + rest : ''), { replace: true });
  }, [auth.user, location.search]); // eslint-disable-line react-hooks/exhaustive-deps

  if (auth.loading && !auth.user) return <LoadingShell />;
  if (!auth.user) return <AuthScreen auth={auth} />;

  const openConversationInChats = (conversationId) => {
    if (!conversationId) return;
    setPendingConversationId(conversationId);
    navigate('/app/chats');
  };

  const openGlobalChatInChats = () => {
    setPendingOpenGlobalChat(true);
    navigate('/app/chats');
  };

  const acceptIncomingCall = async () => {
    const incoming = notify.incomingCall;
    // Asked before the incoming-call card closes, so cancelling the
    // recording notice leaves the call ringing (it can still be declined).
    if (!(await ensureRecordingNotice())) return;
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
      {/* Phone: hamburger-opens-drawer nav matching the Flutter client's
          HomeShell exactly (bottom nav retired there in favor of _AppDrawer
          -- see home_shell.dart's own comment on _showBottomNav). Desktop:
          IconRail, completely unchanged -- the "os" screen renders its own
          full sidebar there (see os/OsApp.jsx), so IconRail stays hidden
          for it same as before. */}
      {isPhone ? (
        <>
          <div className="phone-topbar">
            <button onClick={() => setDrawerOpen(true)} aria-label="Open menu" aria-haspopup="true">
              <MenuIcon />
            </button>
            <span className="phone-topbar-title">{SECTION_TITLES[section] || 'Throughline'}</span>
          </div>
          <AppDrawer
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            active={section}
            notify={notify}
            onOpenConversation={openConversationInChats}
          />
        </>
      ) : (
        section !== 'os' && (
          <IconRail
            active={section}
            onSelect={(s) => navigate('/app/' + s)}
            username={auth.user.username}
            profilePictureUrl={auth.user.profile_picture_url}
            badges={{ tasks: notify.taskCount, chats: notify.unreadChatIds.size }}
            online={notify.connected}
          />
        )
      )}
      <Outlet
        context={{
          auth, notify, call, theme, toggleTheme,
          pendingConversationId,
          openConversationInChats,
          consumePendingConversationId: () => setPendingConversationId(null),
          pendingOpenGlobalChat,
          openGlobalChatInChats,
          consumePendingOpenGlobalChat: () => setPendingOpenGlobalChat(false),
          // false only once we know the questions are unanswered -- Home
          // shows a "Personalize" card then.
          personaPending: personaCompleted === false,
          openPersona: () => setPersonaOpen(true),
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
      <ConsentHost key={userId} onLogout={auth.logout} />
      {personaOpen && (
        <div className="persona-overlay">
          <PersonaOnboarding
            onComplete={() => { setPersonaCompleted(true); setPersonaOpen(false); }}
            onSkip={skipPersona}
          />
        </div>
      )}
    </div>
  );
}

// Throughline Corporate -- its own screen layout under /app/corp, next to the
// Personal side (which is untouched). Pages live in ./pages, shared pieces
// in ./components; everything talks to the backend through corpApi.js.
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import ModeSwitch from '../../components/ModeSwitch.jsx';
import Avatar from '../../components/Avatar.jsx';
import { BuildingIcon, ChatIcon, CheckIcon, FriendIcon, HomeIcon, MicIcon, OrgTreeIcon, PlusIcon, SearchIcon, SettingsIcon, TaskIcon, ChartIcon, TicketIcon } from '../../icons.jsx';
import { useCorpOrg } from './useCorpOrg.js';
import { askDesktopAlerts, desktopAlertsState, useCorpUnread } from '../../lib/corpLive.js';
import { useState } from 'react';
import OverviewPage from './pages/OverviewPage.jsx';
import StructurePage from './pages/StructurePage.jsx';
import PeoplePage from './pages/PeoplePage.jsx';
import SetupPage from './pages/SetupPage.jsx';
import JoinPage from './pages/JoinPage.jsx';
import WelcomePage from './pages/WelcomePage.jsx';
import MessagesPage from './pages/MessagesPage.jsx';
import MeetingsPage, { MeetingDetail } from './pages/MeetingsPage.jsx';
import CommitmentsPage from './pages/CommitmentsPage.jsx';
import DecisionsPage from './pages/DecisionsPage.jsx';
import AskPage from './pages/AskPage.jsx';
import SettingsPage from './pages/SettingsPage.jsx';
import ProjectsPage from './pages/ProjectsPage.jsx';
import ProjectPage from './pages/ProjectPage.jsx';
import TicketsPage from './pages/TicketsPage.jsx';
import TicketPage from './pages/TicketPage.jsx';
import './corporate.css';

const NAV = [
  { to: '/app/corp', label: 'Home', Icon: HomeIcon, end: true },
  { to: '/app/corp/messages', label: 'Messages', Icon: ChatIcon },
  { to: '/app/corp/meetings', label: 'Meetings', Icon: MicIcon },
  { to: '/app/corp/projects', label: 'Projects', Icon: ChartIcon },
  { to: '/app/corp/tickets', label: 'Tickets', Icon: TicketIcon },
  { to: '/app/corp/commitments', label: 'Commitments', Icon: TaskIcon },
  { to: '/app/corp/decisions', label: 'Decisions', Icon: CheckIcon },
  { to: '/app/corp/ask', label: 'Ask', Icon: SearchIcon },
  { to: '/app/corp/structure', label: 'Structure', Icon: OrgTreeIcon },
  { to: '/app/corp/people', label: 'People', Icon: FriendIcon },
  { to: '/app/corp/settings', label: 'Settings', Icon: SettingsIcon },
];

function Unavailable() {
  return (
    <div className="corp-center">
      <div className="corp-empty">
        <BuildingIcon className="corp-empty-icon" />
        <h2>Corporate isn’t switched on yet</h2>
        <p>It’s being built. Switch back to Personal to keep using Throughline as before.</p>
        <ModeSwitch current="corporate" />
      </div>
    </div>
  );
}

function OrgPicker({ corp }) {
  if (corp.orgs.length < 2) return <div className="corp-org-name">{corp.data?.org.name}</div>;
  return (
    <select className="corp-org-select" aria-label="Organisation" value={corp.data?.org.id || ''}
      onChange={(e) => corp.openOrg(Number(e.target.value))}>
      {corp.orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
    </select>
  );
}

export default function CorporateApp({ auth }) {
  const corp = useCorpOrg();
  const unread = useCorpUnread();
  const [alerts, setAlerts] = useState(desktopAlertsState());
  const location = useLocation();
  const user = auth.user;
  const joining = /^\/app\/corp\/join\//.test(location.pathname);

  let body;
  if (joining) {
    body = (
      <Routes>
        <Route path="join/:token" element={<JoinPage user={user} onJoined={(orgId) => corp.reload(orgId)} />} />
      </Routes>
    );
  } else if (corp.status === 'loading') {
    body = <div className="corp-center" role="status"><div className="corp-spinner" aria-label="Loading" /></div>;
  } else if (corp.status === 'unavailable') {
    body = <Unavailable />;
  } else if (corp.status === 'error') {
    body = (
      <div className="corp-center"><div className="corp-empty">
        <h2>Couldn’t load your organisation</h2>
        <p>{corp.error?.message || 'Please try again.'}</p>
        <button className="corp-btn primary" onClick={() => corp.reload()}>Try again</button>
      </div></div>
    );
  } else if (corp.status === 'none') {
    body = <WelcomePage corp={corp} />;
  } else {
    body = (
      <Routes>
        <Route index element={<OverviewPage corp={corp} user={user} />} />
        <Route path="settings" element={<SettingsPage corp={corp} />} />
        <Route path="projects" element={<ProjectsPage corp={corp} />} />
        <Route path="projects/:id" element={<ProjectPage corp={corp} />} />
        <Route path="tickets" element={<TicketsPage corp={corp} />} />
        <Route path="tickets/:ref" element={<TicketPage corp={corp} user={user} />} />
        <Route path="structure" element={<StructurePage corp={corp} user={user} />} />
        <Route path="people" element={<PeoplePage corp={corp} user={user} />} />
        <Route path="messages" element={<MessagesPage corp={corp} user={user} />} />
        <Route path="meetings" element={<MeetingsPage corp={corp} />} />
        <Route path="meetings/:id" element={<MeetingDetail corp={corp} />} />
        <Route path="commitments" element={<CommitmentsPage corp={corp} />} />
        <Route path="decisions" element={<DecisionsPage corp={corp} />} />
        <Route path="ask" element={<AskPage corp={corp} />} />
        <Route path="new" element={<SetupPage corp={corp} />} />
        <Route path="*" element={<Navigate to="/app/corp" replace />} />
      </Routes>
    );
  }

  const showNav = corp.status === 'ready' && !joining;

  return (
    <div className="corp">
      <aside className="corp-sidebar">
        <div className="corp-brand">
          <span className="corp-brand-mark">T</span>
          <span className="corp-brand-text">Throughline</span>
        </div>
        <ModeSwitch current="corporate" />
        {showNav && (
          <>
            <div className="corp-org">
              <span className="corp-org-label">Organisation</span>
              <OrgPicker corp={corp} />
            </div>
            <nav className="corp-nav" aria-label="Corporate">
              {NAV.map(({ to, label, Icon, end }) => (
                <NavLink key={to} to={to} end={end} className={({ isActive }) => 'corp-nav-item' + (isActive ? ' active' : '')}>
                  <Icon aria-hidden="true" /><span>{label}</span>
                  {label === 'Messages' && unread.byOrg[corp.data.org.id] > 0 && (
                    <span className="corp-unread" aria-label={`${unread.byOrg[corp.data.org.id]} unread`}>{unread.byOrg[corp.data.org.id]}</span>)}
                </NavLink>
              ))}
              {corp.canCreate && (
                <NavLink to="/app/corp/new" className={({ isActive }) => 'corp-nav-item subtle' + (isActive ? ' active' : '')}>
                  <PlusIcon aria-hidden="true" /><span>New organisation</span>
                </NavLink>
              )}
            </nav>
          </>
        )}
        {alerts === 'default' && (
          <button className="corp-btn small corp-alerts-btn" onClick={() => askDesktopAlerts().then(setAlerts)}>Turn on message alerts</button>
        )}
        <div className="corp-sidebar-bottom">
          <Avatar url={user.profile_picture_url} name={user.username} />
          <div className="corp-user">
            <div className="corp-user-name">{user.username}</div>
            {corp.data && <div className="corp-user-role">{corp.data.me.role}</div>}
          </div>
        </div>
      </aside>
      <main className="corp-main">{body}</main>
    </div>
  );
}

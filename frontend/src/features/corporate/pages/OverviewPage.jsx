// Corporate home. Built around one question -- "what needs me now?" -- the way
// Linear's Inbox, Asana's Home and Teams' Activity are: actions first,
// then what others owe you and what changed this week, setup only while it's
// unfinished, and settings out of the way (SettingsPage).
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { fmtDate } from '../components/work.jsx';
import { ChatIcon, CheckIcon, MicIcon, PlusIcon, SearchIcon } from '../../../icons.jsx';
import CatchUp from '../components/CatchUp.jsx';
import ClientDigest from '../components/ClientDigest.jsx';

const DAY = 86400000;
const greeting = () => { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'; };
const daysUntil = (d) => (d ? Math.ceil((new Date(d + 'T23:59:59') - Date.now()) / DAY) : null);

function dueText(c) {
  const n = daysUntil(c.due_date);
  if (n === null) return '';
  if (n < 0) return `${-n} day${n === -1 ? '' : 's'} overdue`;
  if (n === 0) return 'due today';
  if (n === 1) return 'due tomorrow';
  return `due ${fmtDate(c.due_date)}`;
}

// One line per item, one obvious action -- details live on the Commitments page.
function Item({ corp, c, why, action, onDone }) {
  const [busy, setBusy] = useState(false);
  const act = async () => {
    setBusy(true);
    try { await corpApi.commitmentAction(corp.data.org.id, c.id, action.kind); onDone(); }
    catch (e) { showToast(e.message, 'error'); setBusy(false); }
  };
  return (
    <li className={'home-item ' + (why.tone || '')}>
      <span className="home-why">{why.label}</span>
      <span className="home-main">
        <span className="home-text">{c.text}</span>
        <span className="home-meta">{[c.unit_name, c.owner_id !== corp.data.me.user_id ? (c.owner || c.owner_name) : null, dueText(c)].filter(Boolean).join(' · ')}</span>
      </span>
      {action && <button className="corp-btn small" onClick={act} disabled={busy}>{action.label}</button>}
    </li>
  );
}

function Setup({ corp }) {
  const navigate = useNavigate();
  const { org, units } = corp.data;
  const depts = units.filter((u) => u.kind === 'department');
  const steps = [
    { done: depts.length > 0, text: 'Add your departments', to: '/app/corp/structure' },
    { done: depts.length > 0 && depts.every((d) => d.managers.length), text: 'Appoint a head for each department', to: '/app/corp/structure' },
    { done: units.some((u) => u.kind === 'team'), text: 'Add teams', to: '/app/corp/structure' },
    { done: org.member_count > 1, text: 'Invite your colleagues', to: '/app/corp/people' },
  ];
  const done = steps.filter((s) => s.done).length;
  const next = steps.find((s) => !s.done);
  if (!next) return null;
  return (
    <section className="home-setup">
      <div className="home-progress" role="progressbar" aria-valuenow={done} aria-valuemin={0} aria-valuemax={steps.length}
        aria-label="Setup progress"><span style={{ width: `${(done / steps.length) * 100}%` }} /></div>
      <span className="corp-muted corp-small">Setup {done} of {steps.length}</span>
      <span className="home-setup-next">Next: {next.text}</span>
      <button className="corp-btn small primary" onClick={() => navigate(next.to)}>Continue</button>
    </section>
  );
}

export default function OverviewPage({ corp, user }) {
  const navigate = useNavigate();
  const { org, me, units } = corp.data;
  const [b, setB] = useState(null);
  const [meetings, setMeetings] = useState([]);
  const load = useCallback(() => {
    corpApi.brief(org.id).then(setB).catch(() => setB({ owe: [], owed: [], to_confirm: [], risks: [], decisions: [] }));
    corpApi.meetings(org.id).then((d) => setMeetings(d.meetings.filter((m) => Date.now() - new Date(m.created_at) < 7 * DAY).slice(0, 4))).catch(() => {});
  }, [org.id]);
  useEffect(() => { load(); }, [load]);

  const mine = units.filter((u) => u.my_role);
  const actions = [
    { label: 'Record a meeting', Icon: MicIcon, to: '/app/corp/meetings' },
    { label: 'Ask', Icon: SearchIcon, to: '/app/corp/ask' },
    { label: 'Message', Icon: ChatIcon, to: '/app/corp/messages' },
    { label: 'Add commitment', Icon: PlusIcon, to: '/app/corp/commitments' },
  ];

  // "Needs you", most urgent first: at risk, to confirm, overdue, due within 7 days.
  let needs = [];
  if (b) {
    const seen = new Set();
    const add = (c, why, action) => { if (!seen.has(c.id)) { seen.add(c.id); needs.push({ c, why, action }); } };
    b.risks.filter((r) => r.commitment).forEach((r) => add(
      { id: r.commitment_id, text: r.commitment, due_date: r.due_date, unit_name: r.commitment_unit, owner_id: me.user_id },
      { label: 'At risk', tone: 'crit' }, null));
    b.to_confirm.filter((c) => c.owner_id === me.user_id).forEach((c) => add(c, { label: 'Confirm', tone: 'info' }, { kind: 'confirm', label: 'Confirm' }));
    // Late by its date even before the 15-minute job marks it overdue.
    const late = (c) => c.status === 'overdue' || (c.status === 'open' && daysUntil(c.due_date) !== null && daysUntil(c.due_date) < 0);
    b.owe.filter(late).forEach((c) => add(c, { label: 'Overdue', tone: 'crit' }, { kind: 'done', label: 'Done' }));
    b.owe.filter((c) => c.status === 'open' && daysUntil(c.due_date) !== null && daysUntil(c.due_date) <= 7)
      .forEach((c) => add(c, { label: 'Due soon', tone: 'warn' }, { kind: 'done', label: 'Done' }));
  }
  const isLate = (c) => c.status === 'overdue' || (daysUntil(c.due_date) !== null && daysUntil(c.due_date) < 0);
  const owed = b ? [...b.owed].sort((x, y) => isLate(y) - isLate(x)) : [];
  const otherRisks = b ? b.risks.filter((r) => !r.commitment) : [];

  return (
    <div className="corp-page home">
      <header className="home-head">
        <div>
          <h1>{greeting()}, {user.username}</h1>
          <p className="corp-muted">
            {new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })} · {org.name}
            {mine.length > 0 && <> · {mine.map((u) => u.name).join(', ')}</>}
          </p>
        </div>
      </header>

      <nav className="home-actions" aria-label="Quick actions">
        {actions.map(({ label, Icon, to }) => (
          <button key={label} className="home-action" onClick={() => navigate(to)}><Icon aria-hidden="true" /><span>{label}</span></button>
        ))}
      </nav>

      {me.org_admin && <Setup corp={corp} />}

      <section className="corp-card home-catchup"><CatchUp corp={corp} /></section>

      <ClientDigest corp={corp} />

      <section className="corp-card home-needs" aria-labelledby="h-needs">
        <h2 id="h-needs">Needs you {b && needs.length > 0 && <span className="home-count">{needs.length}</span>}</h2>
        {!b ? <div className="corp-spinner small" aria-label="Loading" />
          : needs.length === 0 ? (
            <div className="home-clear"><CheckIcon aria-hidden="true" /> You’re all caught up. Nothing overdue, at risk or waiting for you.</div>
          ) : (
            <ul className="home-list">{needs.slice(0, 7).map(({ c, why, action }) => (
              <Item key={c.id} corp={corp} c={c} why={why} action={action} onDone={load} />))}
            </ul>
          )}
        {otherRisks.length > 0 && (
          <p className="corp-small home-note">Your team’s blockers may delay {otherRisks.length} commitment{otherRisks.length === 1 ? '' : 's'} in other teams.
            {' '}<Link className="corp-link" to="/app/corp/commitments">Review</Link></p>
        )}
        {needs.length > 7 && <Link className="corp-link corp-small" to="/app/corp/commitments">See all {needs.length}</Link>}
      </section>

      <div className="home-grid">
        <section className="corp-card" aria-labelledby="h-owed">
          <h2 id="h-owed">Waiting on others</h2>
          {!b ? null : owed.length === 0 ? <p className="corp-muted corp-small">Nobody owes you anything right now.</p> : (
            <ul className="home-list">{owed.slice(0, 5).map((c) => (
              <Item key={c.id} corp={corp} c={c} why={isLate(c) ? { label: 'Late', tone: 'crit' } : { label: 'Open', tone: '' }} />))}
            </ul>
          )}
        </section>
        <section className="corp-card" aria-labelledby="h-week">
          <h2 id="h-week">This week</h2>
          {b && b.decisions.length === 0 && meetings.length === 0 && <p className="corp-muted corp-small">No meetings or decisions in your teams yet this week.</p>}
          <ul className="home-feed">
            {b && b.decisions.slice(0, 4).map((d) => (
              <li key={`d${d.id}`}><span className="home-tag dec">Decision</span>
                <Link className="corp-link" to={`/app/corp/decisions?d=${d.id}`}>{d.title}</Link>
                <span className="home-meta">{d.unit_name}{d.version > 1 ? ` · changed (v${d.version})` : ''}</span></li>))}
            {meetings.map((m) => (
              <li key={`m${m.id}`}><span className="home-tag mtg">Meeting</span>
                <Link className="corp-link" to={`/app/corp/meetings/${m.id}`}>{m.title}</Link>
                <span className="home-meta">{m.unit_name || 'Private'} · {fmtDate(m.created_at)}</span></li>))}
          </ul>
        </section>
      </div>
    </div>
  );
}

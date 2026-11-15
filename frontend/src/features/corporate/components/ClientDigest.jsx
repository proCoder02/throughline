// "Your client projects this week": for Sales / BU people (and anyone on a
// project) -- each client deliverable's progress, what's at risk and why, what's
// due soon, and what got done. The same digest arrives as a Monday notification.
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { fmtDate } from './work.jsx';
import { TicketProgress } from './tickets.jsx';
import { HEALTH } from '../pages/ProjectsPage.jsx';

const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

function Deliverable({ x, tone }) {
  return (
    <li className={'cd-item ' + (tone || '')}>
      <span className="cd-text">{x.text}</span>
      <span className="corp-row-sub">{[x.contact && `for ${x.contact}`, x.due_date && `due ${fmtDate(x.due_date)}`].filter(Boolean).join(' · ')}</span>
      {x.why && <span className="corp-risk">At risk: {x.why}</span>}
      {x.progress && <TicketProgress t={{ ...x.progress, tickets: [] }} compact />}
    </li>
  );
}

export default function ClientDigest({ corp }) {
  const orgId = corp.data.org.id;
  const [d, setD] = useState(null);
  useEffect(() => { corpApi.clientDigest(orgId).then(setD).catch(() => setD({ projects: [] })); }, [orgId]);
  if (!d || d.projects.length === 0) return null;
  const t = d.totals;
  return (
    <section className="corp-card cd" aria-labelledby="h-clients">
      <h2 id="h-clients">Your client projects this week</h2>
      <p className="corp-muted corp-small">
        {[t.at_risk && `${t.at_risk} at risk`, t.due_soon && `${t.due_soon} due in the next 2 weeks`, t.done && `${t.done} delivered`,
          t.tickets_done && `${plural(t.tickets_done, 'ticket')} done`, t.blocked && `${plural(t.blocked, 'ticket')} blocked`].filter(Boolean).join(' · ') || 'All on track.'}
      </p>
      <div className="cd-projects">
        {d.projects.map((p) => {
          const [label, tone] = HEALTH[p.health] || ['', ''];
          return (
            <article key={p.id} className="cd-project">
              <header className="cd-head">
                <Link className="corp-row-title corp-link" to={`/app/corp/projects/${p.id}`}>{p.name}</Link>
                {p.client && <span className="corp-row-sub">{p.client}</span>}
                <span className={'proj-health ' + tone}>{label}</span>
              </header>
              <ul className="cd-list">
                {p.at_risk.map((x) => <Deliverable key={x.id} x={x} tone="risk" />)}
                {p.due_soon.map((x) => <Deliverable key={x.id} x={x} tone="soon" />)}
                {p.on_track.slice(0, 3).map((x) => <Deliverable key={x.id} x={x} />)}
              </ul>
              {(p.done.length > 0 || p.tickets_done > 0) && (
                <p className="corp-small cd-done">✓ {[p.done.length && `${p.done.length} delivered`, p.tickets_done && `${plural(p.tickets_done, 'ticket')} done this week`].filter(Boolean).join(' · ')}</p>)}
              {p.blocked_tickets.length > 0 && (
                <p className="corp-small corp-risk">Blocked: {p.blocked_tickets.map((k) => (
                  <Link key={k.id} className="tk-key-link" to={`/app/corp/tickets/${k.key}`}>{k.key} </Link>))}</p>)}
              <Link className="corp-link corp-small" to={`/app/corp/projects/${p.id}`}>Open project · draft a client update</Link>
            </article>
          );
        })}
      </div>
    </section>
  );
}

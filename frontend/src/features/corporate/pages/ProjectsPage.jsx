// Projects: client engagements. Visible to their members (and admins).
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { fmtDate } from '../components/work.jsx';

export const HEALTH = { on_track: ['On track', 'ok'], at_risk: ['At risk', 'warn'], late: ['Late', 'crit'] };

export default function ProjectsPage({ corp }) {
  const navigate = useNavigate();
  const orgId = corp.data.org.id;
  const [list, setList] = useState(null);
  const [f, setF] = useState({ name: '', client: '', end_date: '' });
  useEffect(() => { corpApi.projects(orgId).then((d) => setList(d.projects)).catch(() => setList([])); }, [orgId]);
  const create = async (e) => {
    e.preventDefault();
    try { const r = await corpApi.createProject(orgId, f); navigate(`/app/corp/projects/${r.id}`); }
    catch (err) { showToast(err.message, 'error'); }
  };
  return (
    <div className="corp-page">
      <header className="corp-page-head"><div><h1>Projects</h1>
        <p className="corp-muted">Each client engagement: who’s who on both sides, what’s promised, and what’s at risk.</p></div></header>
      <form className="corp-card corp-inline wrap" onSubmit={create}>
        <label className="corp-field compact" style={{ flex: 2 }}><span>New project</span>
          <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="e.g. e-Gov citizen portal" maxLength={160} /></label>
        <label className="corp-field compact" style={{ flex: 1 }}><span>Client</span>
          <input value={f.client} onChange={(e) => setF({ ...f, client: e.target.value })} placeholder="e.g. Ministry of IT" maxLength={160} /></label>
        <label className="corp-field compact"><span>Ends</span><input type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></label>
        <button className="corp-btn primary" disabled={!f.name.trim()}>Create</button>
      </form>
      <section className="corp-card">
        {!list ? <div className="corp-spinner small" /> : list.length === 0 ? <p className="corp-muted">No projects yet.</p> : (
          <ul className="corp-list">{list.map((p) => {
            const [label, tone] = p.at_risk ? HEALTH.at_risk : HEALTH.on_track;
            return (
              <li key={p.id}><Link className="corp-row" to={`/app/corp/projects/${p.id}`}>
                <span className="corp-row-main"><span className="corp-row-title">{p.name}</span>
                  <span className="corp-row-sub">{[p.client, p.end_date ? `ends ${fmtDate(p.end_date)}` : null, `${p.open_items} open`,
                    `${p.members} ${p.members === 1 ? 'person' : 'people'}`, p.my_role ? null : 'you’re not a member'].filter(Boolean).join(' · ')}</span></span>
                <span className={'proj-health ' + tone}>{p.at_risk ? `${p.at_risk} at risk` : label}</span>
              </Link></li>);
          })}</ul>
        )}
      </section>
    </div>
  );
}

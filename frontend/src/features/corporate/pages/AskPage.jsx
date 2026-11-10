// Ask about your teams' history. Only content from teams you're in is used.
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { UnitSelect } from '../components/work.jsx';

const LINK = { meeting: (s) => `/app/corp/meetings/${s.id}`, decision: (s) => `/app/corp/decisions?d=${s.id}`,
  commitment: () => '/app/corp/commitments', message: (s) => `/app/corp/messages?t=${s.id}` };
const EXAMPLES = ['What did we decide about the database migration?', 'What is still pending from our last meetings?', 'Who owes what this week?'];

export default function AskPage({ corp }) {
  const [q, setQ] = useState('');
  const [unitId, setUnitId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const ask = async (question) => {
    if (!question.trim()) return;
    setBusy(true); setQ(question);
    try { setRes(await corpApi.ask(corp.data.org.id, question, unitId)); } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };
  return (
    <div className="corp-page">
      <header className="corp-page-head"><div><h1>Ask</h1><p className="corp-muted">Answers come only from meetings, decisions, commitments and chat in teams you’re in.</p></div></header>
      <form className="corp-card corp-form" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <div className="corp-inline wrap">
          <UnitSelect corp={corp} value={unitId} onChange={setUnitId} allowNone="All my teams" />
          <label className="corp-field compact" style={{ flex: 1 }}><span>Question</span>
            <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} placeholder="Ask about your team’s history" /></label>
          <button className="corp-btn primary" disabled={busy || !q.trim()}>{busy ? 'Thinking…' : 'Ask'}</button>
        </div>
        {!res && <div className="corp-chips">{EXAMPLES.map((e) => <button type="button" key={e} className="corp-chip" onClick={() => ask(e)}>{e}</button>)}</div>}
      </form>
      {res && (
        <section className="corp-card" aria-live="polite">
          <p className="corp-answer">{res.answer}</p>
          {res.sources.length > 0 && (<><h3 className="corp-eyebrow">Sources</h3>
            <ul className="corp-list">{res.sources.map((s) => <li key={s.ref}><Link className="corp-link" to={LINK[s.type](s)}>[{s.ref}] {s.title}</Link></li>)}</ul></>)}
        </section>
      )}
    </div>
  );
}

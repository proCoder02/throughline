// Meetings -> team memory. Add one by recording in the browser, pasting
// notes, or importing a
// conversation already recorded with Listen or a call.
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { confirmDialog, showToast } from '../../../lib/notify.js';
import { ensureRecordingNotice } from '../../../lib/consent.js';
import { CommitmentRow, CommonGroundCard, UnitSelect, fmtDate, myUnits } from '../components/work.jsx';
import { SparkleIcon } from '../../../icons.jsx';

const MODES = [['record', 'Record now'], ['text', 'Paste notes'], ['import', 'From my conversations']];

function NewMeeting({ corp, onCreated }) {
  const orgId = corp.data.org.id;
  const [mode, setMode] = useState('record');
  const [unitId, setUnitId] = useState(myUnits(corp)[0]?.id ?? null);
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [convs, setConvs] = useState(null);
  const [convId, setConvId] = useState('');
  const [rec, setRec] = useState(null); // {recorder, chunks, started}
  const [busy, setBusy] = useState(false);
  const [, tick] = useState(0);
  const timer = useRef(null);

  useEffect(() => { if (mode === 'import' && !convs) corpApi.importable(orgId).then((d) => setConvs(d.conversations)).catch(() => setConvs([])); }, [mode, convs, orgId]);
  useEffect(() => () => { clearInterval(timer.current); rec?.recorder.stream.getTracks().forEach((t) => t.stop()); }, [rec]);

  const submit = async (payload) => {
    setBusy(true);
    try { const r = await corpApi.createMeeting(orgId, payload); onCreated(r.id); }
    catch (e) { showToast(e.message, 'error'); }
    finally { setBusy(false); }
  };
  const form = (blob, name, recorded) => {
    const f = new FormData();
    f.append('file', blob, name); f.append('title', title || 'Meeting'); if (unitId) f.append('unit_id', unitId);
    if (recorded) f.append('recorded', '1');
    return f;
  };
  const startRec = async () => {
    if (!(await ensureRecordingNotice())) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.start(1000);
      setRec({ recorder, chunks, started: Date.now() });
      timer.current = setInterval(() => tick((n) => n + 1), 1000);
    } catch { showToast('Microphone not available.', 'error'); }
  };
  const stopRec = () => {
    const { recorder, chunks } = rec;
    recorder.onstop = () => {
      recorder.stream.getTracks().forEach((t) => t.stop());
      clearInterval(timer.current);
      submit(form(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }), 'meeting.webm', true));
      setRec(null);
    };
    recorder.stop();
  };
  const go = (e) => {
    e.preventDefault();
    if (mode === 'text') submit({ title: title || 'Meeting notes', unit_id: unitId, text });
    if (mode === 'import' && convId) submit({ title, unit_id: unitId, conversation_id: Number(convId) });
  };
  const secs = rec ? Math.floor((Date.now() - rec.started) / 1000) : 0;

  return (
    <section className="corp-card">
      <h2>Add a meeting</h2>
      <div className="corp-seg" role="group" aria-label="How">
        {MODES.map(([k, l]) => <button type="button" key={k} className={mode === k ? 'active' : ''} aria-pressed={mode === k} onClick={() => setMode(k)} disabled={!!rec}>{l}</button>)}
      </div>
      <form className="corp-form" onSubmit={go}>
        <div className="corp-inline wrap">
          <UnitSelect corp={corp} value={unitId} onChange={setUnitId} allowNone="Just me (private)" />
          <label className="corp-field compact" style={{ flex: 1 }}><span>Title</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} placeholder="e.g. Database migration" /></label>
        </div>
        {mode === 'record' && (rec
          ? <div className="corp-inline"><span className="corp-rec-dot" aria-hidden="true" /> Recording {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}
              <button type="button" className="corp-btn primary" onClick={stopRec}>Stop and save</button></div>
          : <button type="button" className="corp-btn primary" onClick={startRec} disabled={busy}>Start recording</button>)}
        {mode === 'text' && <textarea rows={6} aria-label="Notes or transcript" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the notes or transcript" />}
        {mode === 'import' && (
          <select aria-label="Conversation" value={convId} onChange={(e) => setConvId(e.target.value)}>
            <option value="">{convs ? (convs.length ? 'Pick a recorded conversation…' : 'No recorded conversations') : 'Loading…'}</option>
            {(convs || []).map((c) => <option key={c.id} value={c.id}>{c.title || 'Untitled'} · {fmtDate(c.created_at)}{c.category === 'office' ? ' · office' : ''}</option>)}
          </select>
        )}
        {mode !== 'record' && (
          <div className="corp-actions"><button className="corp-btn primary" disabled={busy || (mode === 'text' && text.trim().length < 20) || (mode === 'import' && !convId)}>
            {busy ? 'Adding…' : 'Add meeting'}</button></div>
        )}
        <p className="corp-muted corp-small">{unitId ? 'Only members of this team will see it.' : 'Only you will see it.'} Summary, decisions and commitments are pulled out automatically.</p>
      </form>
    </section>
  );
}

export function MeetingDetail({ corp }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const orgId = corp.data.org.id;
  const [m, setM] = useState(null);
  const [cg, setCg] = useState(null);
  const [showT, setShowT] = useState(false);
  const load = () => corpApi.meeting(orgId, id).then(setM).catch((e) => { showToast(e.message, 'error'); navigate('/app/corp/meetings'); });
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (m?.status === 'processing') { const t = setTimeout(load, 3000); return () => clearTimeout(t); } return undefined; }, [m]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!m) return <div className="corp-center"><div className="corp-spinner" aria-label="Loading" /></div>;
  const del = async () => {
    if (!(await confirmDialog({ title: `Delete “${m.title}”?`, danger: true, confirmLabel: 'Delete', message: 'Its transcript and the commitments found in it are deleted.' }))) return;
    try { await corpApi.deleteMeeting(orgId, m.id); navigate('/app/corp/meetings'); } catch (e) { showToast(e.message, 'error'); }
  };
  const findCg = async () => { try { setCg(await corpApi.meetingCommonGround(orgId, m.id)); } catch (e) { showToast(e.message, 'error'); } };
  return (
    <div className="corp-page">
      <header className="corp-page-head"><div>
        <div className="corp-eyebrow"><Link className="corp-link" to="/app/corp/meetings">Meetings</Link> · {m.unit_name || 'Private'} · {fmtDate(m.created_at)}</div>
        <h1>{m.title}</h1></div>
        <button className="corp-btn danger small" onClick={del}>Delete</button></header>
      {m.status === 'processing' && <div className="corp-card corp-inline"><div className="corp-spinner small" /> Transcribing and reading the meeting…</div>}
      {m.status === 'failed' && <div className="corp-card corp-warn">Couldn’t process this meeting: {m.error}</div>}
      {m.status === 'ready' && (<>
        <section className="corp-card"><h2>Summary</h2><p>{m.summary || 'No summary.'}</p>
          <button className="corp-btn small" onClick={findCg}><SparkleIcon aria-hidden="true" /> Find common ground</button></section>
        {cg && <CommonGroundCard corp={corp} id={cg.id} result={cg.result} usedNotes={cg.used_notes} />}
        <section className="corp-card"><h2>Decisions ({m.decisions.length})</h2>
          {m.decisions.length ? <ul className="corp-list">{m.decisions.map((d) => <li key={`${d.id}-${d.version}`} className="corp-person"><span className="corp-row-main">
            <Link className="corp-row-title" to={`/app/corp/decisions?d=${d.id}`}>{d.title}</Link><span className="corp-row-sub">{d.text}{d.quote ? ` — “${d.quote}”` : ''}</span></span></li>)}</ul>
            : <p className="corp-muted">None found.</p>}</section>
        <section className="corp-card"><h2>Commitments ({m.commitments.length})</h2>
          {m.commitments.length ? <ul className="corp-list">{m.commitments.map((c) => <CommitmentRow key={c.id} corp={corp} c={c} onChanged={load} />)}</ul>
            : <p className="corp-muted">None found.</p>}</section>
        {m.blockers.length > 0 && <section className="corp-card"><h2>Blockers</h2><ul>{m.blockers.map((b) => <li key={b.id}>{b.text}</li>)}</ul></section>}
        <section className="corp-card"><button className="corp-link" onClick={() => setShowT((v) => !v)}>{showT ? 'Hide transcript' : 'Show transcript'}</button>
          {showT && <pre className="corp-transcript">{m.transcript}</pre>}</section>
      </>)}
    </div>
  );
}

export default function MeetingsPage({ corp }) {
  const navigate = useNavigate();
  const [list, setList] = useState(null);
  useEffect(() => { corpApi.meetings(corp.data.org.id).then((d) => setList(d.meetings)).catch(() => setList([])); }, [corp.data.org.id]);
  return (
    <div className="corp-page">
      <header className="corp-page-head"><div><h1>Meetings</h1><p className="corp-muted">Each team’s meetings become its memory.</p></div></header>
      <NewMeeting corp={corp} onCreated={(id) => navigate(`/app/corp/meetings/${id}`)} />
      <section className="corp-card">
        {!list ? <div className="corp-spinner small" /> : list.length === 0 ? <p className="corp-muted">No meetings yet.</p> : (
          <ul className="corp-list">{list.map((m) => (
            <li key={m.id}><Link className="corp-row" to={`/app/corp/meetings/${m.id}`}>
              <span className="corp-row-main"><span className="corp-row-title">{m.title}</span>
                <span className="corp-row-sub">{m.unit_name || 'Private'} · {fmtDate(m.created_at)} · {m.status === 'ready' ? (m.summary || '').slice(0, 110) : m.status}</span></span>
            </Link></li>))}</ul>
        )}
      </section>
    </div>
  );
}

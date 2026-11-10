// Invite people by email -- one address or a pasted list (a column copied
// from a spreadsheet works). Optionally straight into a department or team.
// Each invitation's link is shown once, to copy, in case the email doesn't arrive.
import { useEffect, useRef, useState } from 'react';
import { corpApi, managerLabel } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { CopyIcon } from '../../../icons.jsx';

const TEMPLATE = 'email,team,team_role,role\nname@company.com,Database Team,member,member\nlead@company.com,Backend,lead,member\n';

// A spreadsheet saved as CSV: email is required; team (exact name from Structure),
// team_role (member / head / lead) and role (member / admin) are optional.
export function parseCsv(text) {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return [];
  const cells = (l) => l.split(',').map((c) => c.trim().replace(/^"(.*)"$/, '$1'));
  let cols = ['email', 'team', 'team_role', 'role'];
  const first = cells(lines[0]).map((c) => c.toLowerCase().replace(/\s+/g, '_'));
  if (first.includes('email')) { cols = first; lines.shift(); }
  return lines.map((l) => Object.fromEntries(cells(l).map((v, i) => [cols[i] || `col${i}`, v])));
}

export default function InviteForm({ corp, presetUnitId, prefill, onSent }) {
  const { org, me, units } = corp.data;
  const invitable = units.filter((u) => u.manage_people);
  const [emails, setEmails] = useState('');
  const [role, setRole] = useState('member');
  const [unitId, setUnitId] = useState(presetUnitId && invitable.some((u) => u.id === presetUnitId) ? String(presetUnitId) : '');
  const [unitRole, setUnitRole] = useState('member');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);

  const unit = units.find((u) => String(u.id) === unitId);
  const emailsRef = useRef(null);

  // "Invite <email>" from the directory search: fill the box and bring it into view.
  useEffect(() => {
    if (!prefill) return;
    setEmails(prefill.email);
    setResult(null);
    emailsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    emailsRef.current?.focus();
  }, [prefill]);
  const needsUnit = !me.org_admin; // managers invite into their own units

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await corpApi.invite(org.id, {
        emails, role, unitId: unitId ? Number(unitId) : null, unitRole: unit ? unitRole : undefined,
      });
      setResult(r);
      if (r.created.length) setEmails('');
      onSent?.();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const uploadCsv = async (file) => {
    if (!file) return;
    const rows = parseCsv(await file.text());
    if (!rows.length) { showToast('That file has no rows.', 'error'); return; }
    if (rows.length > 200) { showToast('Up to 200 people per file. Split it into smaller files.', 'error'); return; }
    setBusy(true);
    try { setResult(await corpApi.inviteRows(org.id, rows)); onSent?.(); }
    catch (err) { showToast(err.message, 'error'); }
    finally { setBusy(false); }
  };

  const copy = (link) => {
    navigator.clipboard?.writeText(link).then(() => showToast('Link copied.', 'success'), () => showToast('Couldn’t copy.', 'error'));
  };

  return (
    <section className="corp-card">
      <h2>Invite people</h2>
      <form className="corp-form" onSubmit={submit}>
        <label className="corp-field">
          <span>Email addresses</span>
          <textarea ref={emailsRef} rows={3} value={emails} onChange={(e) => setEmails(e.target.value)} required
            placeholder="one@company.com, two@company.com — or paste a column from a spreadsheet" />
        </label>
        <div className="corp-inline wrap">
          <label className="corp-field compact">
            <span>Department or team</span>
            <select value={unitId} onChange={(e) => setUnitId(e.target.value)} required={needsUnit}>
              {!needsUnit && <option value="">Organisation only</option>}
              {needsUnit && <option value="">Choose…</option>}
              {invitable.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          </label>
          {unit && unit.edit && (
            <label className="corp-field compact">
              <span>Their role there</span>
              <select value={unitRole} onChange={(e) => setUnitRole(e.target.value)}>
                <option value="member">Member</option>
                <option value="manager">{managerLabel(unit.kind)}</option>
              </select>
            </label>
          )}
          {me.org_owner && (
            <label className="corp-field compact">
              <span>Organisation role</span>
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </label>
          )}
        </div>
        <div className="corp-actions">
          <div className="corp-csv">
            <label className="corp-btn">Upload a CSV
              <input type="file" accept=".csv,text/csv" hidden disabled={busy}
                onChange={(e) => { uploadCsv(e.target.files[0]); e.target.value = ''; }} />
            </label>
            <a className="corp-link corp-small" download="throughline-invites.csv"
              href={`data:text/csv;charset=utf-8,${encodeURIComponent(TEMPLATE)}`}>Template</a>
          </div>
          <span style={{ flex: 1 }} />
          <button className="corp-btn primary" disabled={busy || !emails.trim() || (needsUnit && !unitId)}>
            {busy ? 'Sending…' : 'Send invitations'}
          </button>
        </div>
      </form>

      {result && (
        <div className="corp-invite-result" role="status">
          {result.created.length > 0 && (
            <>
              <p>
                {result.created.length} invitation{result.created.length === 1 ? '' : 's'}{' '}
                {result.emailed ? 'emailed' : 'created'}. Each link works once, for 14 days, and only for that email address.
              </p>
              <ul className="corp-list">
                {result.created.map((c) => (
                  <li key={c.id} className="corp-invite-link">
                    <span className="corp-row-title">{c.email}{c.team ? <span className="corp-muted"> · {c.team}</span> : null}</span>
                    <button className="corp-btn small" onClick={() => copy(c.link)}><CopyIcon aria-hidden="true" /> Copy link</button>
                  </li>
                ))}
              </ul>
            </>
          )}
          {result.already_members.length > 0 && <p className="corp-muted">Already in the organisation: {result.already_members.join(', ')}</p>}
          {result.invalid.length > 0 && <p className="corp-warn">Not valid email addresses: {result.invalid.join(', ')}</p>}
          {result.errors?.length > 0 && (
            <ul className="corp-warn corp-small">{result.errors.map((e) => <li key={e.email}>{e.email}: {e.error}</li>)}</ul>
          )}
        </div>
      )}
    </section>
  );
}

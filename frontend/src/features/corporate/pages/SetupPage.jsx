// Create an organisation: its name, and optionally its first departments.
// The creator becomes its owner. Joining an existing one happens through an
// invitation link instead (see JoinPage).
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';
import { BuildingIcon, CloseIcon } from '../../../icons.jsx';

const SUGGESTED = ['Engineering', 'Product', 'Sales', 'Marketing', 'Finance', 'HR', 'Operations'];

export default function SetupPage({ corp, first = false }) {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [departments, setDepartments] = useState([]);
  const [custom, setCustom] = useState('');
  const [saving, setSaving] = useState(false);

  const has = (d) => departments.some((x) => x.toLowerCase() === d.toLowerCase());
  const toggle = (d) => setDepartments((list) => (has(d) ? list.filter((x) => x.toLowerCase() !== d.toLowerCase()) : [...list, d]));
  const addCustom = () => {
    const d = custom.trim();
    if (d && !has(d)) setDepartments((list) => [...list, d]);
    setCustom('');
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true);
    try {
      const d = await corpApi.createOrg(name.trim(), departments);
      await corp.reload(d.org.id);
      showToast(`${d.org.name} is set up.`, 'success');
      navigate('/app/corp/structure');
    } catch (err) {
      showToast(err.message, 'error');
      setSaving(false);
    }
  };

  return (
    <div className="corp-page narrow">
      <header className="corp-page-head">
        <BuildingIcon className="corp-head-icon" aria-hidden="true" />
        <div>
          <h1>{first ? 'Set up your organisation' : 'New organisation'}</h1>
          <p className="corp-muted">
            {first
              ? 'You’ll be its owner. Then you can add departments and teams, and invite colleagues. '
                + 'Been invited by your company? Open the link from your invitation email instead.'
              : 'You’ll be its owner.'}
          </p>
        </div>
      </header>

      <form className="corp-card corp-form" onSubmit={submit}>
        <label className="corp-field">
          <span>Organisation name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required
            placeholder="e.g. ABC Technologies" autoFocus />
        </label>

        <div className="corp-field">
          <span>Departments <em className="corp-muted">(optional, you can add more later)</em></span>
          <div className="corp-chips">
            {SUGGESTED.map((d) => (
              <button type="button" key={d} className={'corp-chip' + (has(d) ? ' on' : '')}
                aria-pressed={has(d)} onClick={() => toggle(d)}>{d}</button>
            ))}
            {departments.filter((d) => !SUGGESTED.includes(d)).map((d) => (
              <span key={d} className="corp-chip on">
                {d}
                <button type="button" className="corp-chip-x" aria-label={`Remove ${d}`} onClick={() => toggle(d)}>
                  <CloseIcon />
                </button>
              </span>
            ))}
          </div>
          <div className="corp-inline">
            <input value={custom} onChange={(e) => setCustom(e.target.value)} maxLength={120}
              placeholder="Another department"
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCustom(); } }} />
            <button type="button" className="corp-btn" onClick={addCustom} disabled={!custom.trim()}>Add department</button>
          </div>
        </div>

        <div className="corp-actions">
          {!first && <button type="button" className="corp-btn" onClick={() => navigate('/app/corp')}>Cancel</button>}
          <button className="corp-btn primary" disabled={!name.trim() || saving}>
            {saving ? 'Creating…' : 'Create organisation'}
          </button>
        </div>
      </form>
    </div>
  );
}

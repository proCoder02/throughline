// Inline form to add a department or team -- at the top level (parent
// null, admins only) or under a unit.
import { useState } from 'react';
import { corpApi } from '../corpApi.js';
import { showToast } from '../../../lib/notify.js';

export default function AddUnitForm({ corp, parent, onDone, onCancel }) {
  // A department can't sit inside a team.
  const kinds = parent?.kind === 'team' ? ['team'] : parent ? ['team', 'department'] : ['department'];
  const [kind, setKind] = useState(kinds[0]);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const d = await corpApi.createUnit(corp.data.org.id, { name: name.trim(), kind, parentId: parent?.id });
      corp.apply(d);
      setName('');
      onDone?.(d.id);
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  const noun = kind === 'team' ? 'team' : parent ? 'sub-department' : 'department';
  return (
    <form className="corp-add-unit" onSubmit={submit}>
      {kinds.length > 1 && (
        <div className="corp-seg" role="group" aria-label="Kind">
          {kinds.map((k) => (
            <button type="button" key={k} className={k === kind ? 'active' : ''} aria-pressed={k === kind}
              onClick={() => setKind(k)}>{k === 'team' ? 'Team' : 'Sub-department'}</button>
          ))}
        </div>
      )}
      <div className="corp-inline">
        <input aria-label={`New ${noun} name`} placeholder={`New ${noun} name`} value={name}
          onChange={(e) => setName(e.target.value)} maxLength={120} autoFocus />
        <button className="corp-btn primary" disabled={busy || !name.trim()}>Add {noun}</button>
        {onCancel && <button type="button" className="corp-btn" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}

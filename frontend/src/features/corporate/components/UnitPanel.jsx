// One department or team: its people, its manager(s), and -- for those
// allowed -- renaming, deleting, adding units under it and changing who's in it.
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Avatar from '../../../components/Avatar.jsx';
import { corpApi, managerLabel } from '../corpApi.js';
import { confirmDialog, showToast } from '../../../lib/notify.js';
import { PencilIcon, PlusIcon, TrashIcon } from '../../../icons.jsx';
import AddUnitForm from './AddUnitForm.jsx';

function AddPerson({ orgId, unit, members, canAppoint, onChanged }) {
  const [directory, setDirectory] = useState(null);
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState('member');
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    corpApi.members(orgId).then((d) => setDirectory(d.members)).catch(() => setDirectory([]));
  }, [orgId]);

  const inUnit = new Set(members.map((m) => m.user_id));
  const options = (directory || []).filter((m) => !inUnit.has(m.user_id));

  const add = async (e) => {
    e.preventDefault();
    if (!userId) return;
    setBusy(true);
    try {
      await corpApi.putUnitMember(orgId, unit.id, Number(userId), role);
      setUserId('');
      setRole('member');
      onChanged();
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="corp-add-person" onSubmit={add}>
      <div className="corp-inline wrap">
        <select aria-label="Person to add" value={userId} onChange={(e) => setUserId(e.target.value)} disabled={!directory}>
          <option value="">{directory ? (options.length ? 'Add someone from the organisation…' : 'Everyone is already here') : 'Loading…'}</option>
          {options.map((m) => <option key={m.user_id} value={m.user_id}>{m.username}{m.title ? ` — ${m.title}` : ''}</option>)}
        </select>
        {canAppoint && (
          <select aria-label="Role in this unit" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="member">Member</option>
            <option value="manager">{managerLabel(unit.kind)}</option>
          </select>
        )}
        <button className="corp-btn primary" disabled={busy || !userId}>Add person</button>
      </div>
      <button type="button" className="corp-link" onClick={() => navigate(`/app/corp/people?invite=${unit.id}`)}>
        Not in the organisation yet? Invite them
      </button>
    </form>
  );
}

export default function UnitPanel({ corp, unit, onDeleted, onSelect }) {
  const orgId = corp.data.org.id;
  const [members, setMembers] = useState(null);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(unit.name);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    corpApi.unitMembers(orgId, unit.id).then((d) => setMembers(d.members)).catch((e) => showToast(e.message, 'error'));
  }, [orgId, unit.id]);
  useEffect(() => { setMembers(null); setName(unit.name); setRenaming(false); setAdding(false); load(); }, [load, unit.name]);

  // Counts and managers in the tree come from the organisation view.
  const changed = () => { load(); corp.openOrg(orgId).catch(() => {}); };

  const rename = async (e) => {
    e.preventDefault();
    try { corp.apply(await corpApi.renameUnit(orgId, unit.id, name)); setRenaming(false); } catch (err) { showToast(err.message, 'error'); }
  };
  const remove = async () => {
    const below = corp.data.units.filter((u) => u.parent_id === unit.id).length;
    const ok = await confirmDialog({
      title: `Delete ${unit.name}?`, danger: true, confirmLabel: 'Delete',
      message: (below ? 'Everything under it is deleted too. ' : '')
        + 'People stay in the organisation; only their place in this unit goes.',
    });
    if (!ok) return;
    try { corp.apply(await corpApi.deleteUnit(orgId, unit.id)); onDeleted(); } catch (err) { showToast(err.message, 'error'); }
  };
  const setRole = async (m, role) => {
    try { await corpApi.putUnitMember(orgId, unit.id, m.user_id, role); changed(); } catch (err) { showToast(err.message, 'error'); }
  };
  const removeMember = async (m) => {
    const ok = await confirmDialog({ title: `Remove ${m.username} from ${unit.name}?`, confirmLabel: 'Remove',
      message: 'They stay in the organisation.' });
    if (!ok) return;
    try { await corpApi.removeUnitMember(orgId, unit.id, m.user_id); changed(); } catch (err) { showToast(err.message, 'error'); }
  };

  const parent = corp.data.units.find((u) => u.id === unit.parent_id);
  const mLabel = managerLabel(unit.kind);

  return (
    <section className="corp-panel" aria-label={unit.name}>
      <header className="corp-panel-head">
        {renaming ? (
          <form className="corp-inline" onSubmit={rename}>
            <input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} autoFocus />
            <button className="corp-btn primary" disabled={!name.trim()}>Save</button>
            <button type="button" className="corp-btn" onClick={() => { setRenaming(false); setName(unit.name); }}>Cancel</button>
          </form>
        ) : (
          <>
            <div>
              <div className="corp-eyebrow">
                {unit.kind === 'team' ? 'Team' : parent ? 'Sub-department' : 'Department'}
                {parent && <> in <button className="corp-link" onClick={() => onSelect(parent.id)}>{parent.name}</button></>}
              </div>
              <h2>{unit.name}</h2>
            </div>
            {unit.edit && (
              <div className="corp-icon-actions">
                <button className="corp-icon-btn" title="Rename" aria-label="Rename" onClick={() => setRenaming(true)}><PencilIcon /></button>
                <button className="corp-icon-btn danger" title="Delete" aria-label="Delete" onClick={remove}><TrashIcon /></button>
              </div>
            )}
          </>
        )}
      </header>

      <div className="corp-panel-section">
        <h3>{mLabel}{unit.managers.length > 1 ? 's' : ''}</h3>
        {unit.managers.length ? (
          <p>{unit.managers.map((m) => m.username).join(', ')}</p>
        ) : (
          <p className="corp-muted">None yet.{unit.edit ? ` Add someone below as ${mLabel.toLowerCase()}.` : ''}</p>
        )}
      </div>

      <div className="corp-panel-section">
        <h3>People ({members ? members.length : unit.member_count})</h3>
        {!members ? (
          <div className="corp-spinner small" aria-label="Loading" />
        ) : members.length === 0 ? (
          <p className="corp-muted">Nobody here yet.</p>
        ) : (
          <ul className="corp-list">
            {members.map((m) => {
              const canChange = m.role === 'manager' ? unit.edit : unit.manage_people;
              return (
                <li key={m.user_id} className="corp-person">
                  <Avatar url={m.profile_picture_url} name={m.username} />
                  <span className="corp-row-main">
                    <span className="corp-row-title">{m.username}</span>
                    <span className="corp-row-sub">{m.role === 'manager' ? mLabel : 'Member'}{m.title ? ` · ${m.title}` : ''}</span>
                  </span>
                  {unit.edit && (
                    <button className="corp-btn small" onClick={() => setRole(m, m.role === 'manager' ? 'member' : 'manager')}>
                      {m.role === 'manager' ? 'Make member' : `Make ${mLabel.toLowerCase()}`}
                    </button>
                  )}
                  {canChange && (
                    <button className="corp-icon-btn" title="Remove from unit" aria-label={`Remove ${m.username} from ${unit.name}`}
                      onClick={() => removeMember(m)}><TrashIcon /></button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {unit.manage_people && members && (
          <AddPerson orgId={orgId} unit={unit} members={members} canAppoint={unit.edit} onChanged={changed} />
        )}
      </div>

      {unit.create_child && (
        <div className="corp-panel-section">
          <h3>Under {unit.name}</h3>
          {adding ? (
            <AddUnitForm corp={corp} parent={unit} onDone={(id) => { setAdding(false); onSelect(id); }}
              onCancel={() => setAdding(false)} />
          ) : (
            <button className="corp-btn" onClick={() => setAdding(true)}>
              <PlusIcon aria-hidden="true" /> Add {unit.kind === 'team' ? 'a sub-team' : 'a team or sub-department'}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

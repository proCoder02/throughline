// The organisation's directory, invitations, and organisation roles.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import Avatar from '../../../components/Avatar.jsx';
import { corpApi, managerLabel, ORG_ROLE_LABELS } from '../corpApi.js';
import { confirmDialog, showToast } from '../../../lib/notify.js';
import { SearchIcon, TrashIcon } from '../../../icons.jsx';
import InviteForm from '../components/InviteForm.jsx';

const looksLikeEmail = (v) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v.trim());

function PendingInvites({ corp, version }) {
  const orgId = corp.data.org.id;
  const [list, setList] = useState(null);
  useEffect(() => {
    corpApi.invites(orgId).then((d) => setList(d.invites)).catch(() => setList([]));
  }, [orgId, version]);
  const pending = (list || []).filter((i) => i.status === 'pending' || i.status === 'expired');
  if (!list || pending.length === 0) return null;

  const revoke = async (inv) => {
    const ok = await confirmDialog({ title: `Withdraw the invitation to ${inv.email}?`, confirmLabel: 'Withdraw' });
    if (!ok) return;
    try { setList((await corpApi.revokeInvite(orgId, inv.id)).invites); } catch (e) { showToast(e.message, 'error'); }
  };

  return (
    <section className="corp-card">
      <h2>Waiting to join ({pending.length})</h2>
      <ul className="corp-list">
        {pending.map((i) => (
          <li key={i.id} className="corp-person">
            <span className="corp-row-main">
              <span className="corp-row-title">{i.email}</span>
              <span className="corp-row-sub">
                {i.unit_name ? `${i.unit_name} · ` : ''}{i.role === 'admin' ? 'Admin · ' : ''}
                {i.status === 'expired' ? 'Expired' : `Expires ${new Date(i.expires_at).toLocaleDateString()}`}
                {i.invited_by ? ` · invited by ${i.invited_by}` : ''}
              </span>
            </span>
            <button className="corp-icon-btn" title="Withdraw" aria-label={`Withdraw invitation to ${i.email}`} onClick={() => revoke(i)}>
              <TrashIcon />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function MemberRow({ m, corp, user, onChanged, everyone }) {
  const { org, me } = corp.data;
  const isMe = m.user_id === user.id;
  const [editingTitle, setEditingTitle] = useState(false);
  const [title, setTitle] = useState(m.title || '');

  const run = async (fn) => { try { onChanged(await fn()); } catch (e) { showToast(e.message, 'error'); } };
  const changeRole = (role) => run(() => corpApi.updateMember(org.id, m.user_id, { role }));
  const saveTitle = (e) => {
    e.preventDefault();
    run(async () => { const d = await corpApi.updateMember(org.id, m.user_id, { title }); setEditingTitle(false); return d; });
  };
  // Leavers: their open commitments go to someone else (or stay ownerless, flagged), never silently lost.
  const [removing, setRemoving] = useState(false);
  const [heir, setHeir] = useState('');
  const remove = () => run(async () => {
    const d = await corpApi.removeMember(org.id, m.user_id, heir ? Number(heir) : null);
    if (d.handed_over) showToast(`${d.handed_over} open commitment${d.handed_over === 1 ? '' : 's'} handed over.`, 'success');
    return d;
  });
  const setAccess = async (status) => {
    if (status === 'suspended' && !(await confirmDialog({ title: `Pause ${m.username}'s access?`, confirmLabel: 'Pause access',
      message: 'They keep their place, teams and history, but can’t open the organisation until you restore access.' }))) return;
    run(() => corpApi.setAccess(org.id, m.user_id, status));
  };

  const navigate = useNavigate();
  const message = async () => {
    try { const r = await corpApi.direct(org.id, m.user_id); navigate(`/app/corp/messages?t=${r.thread_id}`); } catch (e) { showToast(e.message, 'error'); }
  };
  const canRemove = !isMe && m.role !== 'owner' && (m.role === 'admin' ? me.org_owner : me.org_admin);
  const suspended = m.status === 'suspended';
  const canEditTitle = me.org_admin || isMe;

  return (
    <li className="corp-person">
      <Avatar url={m.profile_picture_url} name={m.username} />
      <span className="corp-row-main">
        <span className="corp-row-title">{m.username}{isMe ? ' (you)' : ''}{suspended && <span className="corp-status overdue corp-gap">Access paused</span>}</span>
        {editingTitle ? (
          <form className="corp-inline" onSubmit={saveTitle}>
            <input aria-label="Job title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} autoFocus />
            <button className="corp-btn small primary">Save</button>
            <button type="button" className="corp-btn small" onClick={() => setEditingTitle(false)}>Cancel</button>
          </form>
        ) : (
          <span className="corp-row-sub">
            {m.title || (canEditTitle ? '' : '—')}
            {canEditTitle && <button className="corp-link" onClick={() => setEditingTitle(true)}>{m.title ? ' Edit' : 'Add job title'}</button>}
            {m.email ? ` · ${m.email}` : ''}
          </span>
        )}
        {m.units.length > 0 && (
          <span className="corp-row-units">
            {m.units.map((u) => (
              <span key={u.id} className="corp-tag">{u.name}{u.role === 'manager' ? ` · ${managerLabel(u.kind)}` : ''}</span>
            ))}
          </span>
        )}
      </span>
      {!isMe && <button className="corp-btn small" onClick={message}>Message</button>}
      {me.org_owner && m.role !== 'owner' ? (
        <select aria-label={`Organisation role of ${m.username}`} value={m.role} onChange={(e) => changeRole(e.target.value)}>
          <option value="member">Member</option>
          <option value="admin">Admin</option>
        </select>
      ) : (
        <span className={'corp-role ' + m.role}>{ORG_ROLE_LABELS[m.role]}</span>
      )}
      {canRemove && (
        <button className="corp-btn small" onClick={() => setAccess(suspended ? 'active' : 'suspended')}>
          {suspended ? 'Restore access' : 'Pause access'}
        </button>
      )}
      {canRemove && (
        <button className="corp-icon-btn danger" title="Remove from organisation" aria-label={`Remove ${m.username}`} onClick={() => setRemoving((v) => !v)}>
          <TrashIcon />
        </button>
      )}
      {removing && (
        <div className="corp-remove-panel" role="group" aria-label={`Remove ${m.username}`}>
          <p>Remove <strong>{m.username}</strong> from {org.name}? They leave every department and team.</p>
          <label className="corp-field compact"><span>Hand their open commitments to</span>
            <select value={heir} onChange={(e) => setHeir(e.target.value)}>
              <option value="">No one yet (marked “needs an owner”)</option>
              {everyone.filter((x) => x.user_id !== m.user_id && x.status !== 'suspended').map((x) => (
                <option key={x.user_id} value={x.user_id}>{x.username}</option>))}
            </select>
          </label>
          <div className="corp-actions">
            <button className="corp-btn" onClick={() => setRemoving(false)}>Cancel</button>
            <button className="corp-btn danger" onClick={remove}>Remove</button>
          </div>
        </div>
      )}
    </li>
  );
}

export default function PeoplePage({ corp, user }) {
  const [params] = useSearchParams();
  const { org, me } = corp.data;
  const [members, setMembers] = useState(null);
  const [query, setQuery] = useState('');
  const [inviteVersion, setInviteVersion] = useState(0);
  const [prefill, setPrefill] = useState(null); // {email, n} from the search box

  const load = useCallback(() => {
    corpApi.members(org.id).then((d) => setMembers(d.members)).catch((e) => showToast(e.message, 'error'));
  }, [org.id]);
  useEffect(() => { load(); }, [load]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !members) return members || [];
    return members.filter((m) => [m.username, m.email, m.title, ...m.units.map((u) => u.name)]
      .some((v) => (v || '').toLowerCase().includes(q)));
  }, [members, query]);

  const changed = (d) => {
    if (d?.members) setMembers(d.members);
    corp.openOrg(org.id).catch(() => {});
  };

  return (
    <div className="corp-page">
      <header className="corp-page-head">
        <div>
          <h1>People</h1>
          <p className="corp-muted">{org.member_count} {org.member_count === 1 ? 'person' : 'people'} in {org.name}.</p>
        </div>
      </header>

      {me.can_invite && (
        <InviteForm corp={corp} presetUnitId={Number(params.get('invite')) || null} prefill={prefill}
          onSent={() => { setInviteVersion((v) => v + 1); setQuery(''); }} />
      )}
      {me.can_invite && <PendingInvites corp={corp} version={inviteVersion} />}

      <section className="corp-card">
        <div className="corp-search">
          <SearchIcon aria-hidden="true" />
          <input aria-label="Search people" placeholder="Search people, or type an email to invite" value={query}
            onChange={(e) => setQuery(e.target.value)} />
        </div>
        {!members ? (
          <div className="corp-spinner small" aria-label="Loading" />
        ) : shown.length === 0 ? (
          looksLikeEmail(query) && me.can_invite ? (
            <div className="corp-pad corp-nomatch">
              <p className="corp-muted">{query.trim()} isn’t in {org.name} yet.</p>
              <button className="corp-btn primary" onClick={() => setPrefill({ email: query.trim(), n: Date.now() })}>
                Invite {query.trim()}
              </button>
            </div>
          ) : (
            <p className="corp-muted corp-pad">
              No one in {org.name} matches “{query}”.{me.can_invite ? ' To add someone new, type their email address or use Invite people above.' : ''}
            </p>
          )
        ) : (
          <ul className="corp-list">
            {shown.map((m) => <MemberRow key={m.user_id} m={m} corp={corp} user={user} onChanged={changed} everyone={members} />)}
          </ul>
        )}
      </section>
    </div>
  );
}

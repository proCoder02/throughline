// Web client for the backend's corporate/ package (/corp/...).
import { api, apiJson, post, postForm } from '../../api.js';

export class CorpUnavailable extends Error {}

const json = (body) => ({ body: JSON.stringify(body) });

// With CORPORATE_ENABLED off, /corp/* falls through to the SPA page (HTML).
async function me() {
  const res = await api('/corp/me');
  const data = await res.json().catch(() => { throw new CorpUnavailable(); });
  if (!res.ok) throw Object.assign(new Error(data.error || 'Request failed'), { status: res.status });
  return data;
}

const org = (id) => `/corp/orgs/${id}`;

export const corpApi = {
  me,
  createOrg: (name, departments) => post('/corp/orgs', { name, departments }),
  getOrg: (id) => apiJson(org(id)),
  renameOrg: (id, name) => apiJson(org(id), { method: 'PATCH', ...json({ name }) }),
  deleteOrg: (id, confirmName) => apiJson(org(id), { method: 'DELETE', ...json({ confirm_name: confirmName }) }),
  leaveOrg: (id) => post(`${org(id)}/leave`, {}),

  createUnit: (id, { name, kind, parentId }) => post(`${org(id)}/units`, { name, kind, parent_id: parentId ?? null }),
  renameUnit: (id, unitId, name) => apiJson(`${org(id)}/units/${unitId}`, { method: 'PATCH', ...json({ name }) }),
  deleteUnit: (id, unitId) => apiJson(`${org(id)}/units/${unitId}`, { method: 'DELETE' }),
  unitMembers: (id, unitId) => apiJson(`${org(id)}/units/${unitId}/members`),
  putUnitMember: (id, unitId, userId, role) =>
    apiJson(`${org(id)}/units/${unitId}/members/${userId}`, { method: 'PUT', ...json({ role }) }),
  removeUnitMember: (id, unitId, userId) =>
    apiJson(`${org(id)}/units/${unitId}/members/${userId}`, { method: 'DELETE' }),

  members: (id) => apiJson(`${org(id)}/members`),
  updateMember: (id, userId, changes) =>
    apiJson(`${org(id)}/members/${userId}`, { method: 'PATCH', ...json(changes) }),
  removeMember: (id, userId, reassignTo = null) =>
    apiJson(`${org(id)}/members/${userId}`, { method: 'DELETE', ...json({ reassign_to: reassignTo }) }),
  setAccess: (id, userId, status) => apiJson(`${org(id)}/members/${userId}`, { method: 'PATCH', ...json({ status }) }),
  inviteRows: (id, rows) => post(`${org(id)}/invites`, { rows }),
  myInvites: () => apiJson('/corp/invites/mine'),
  acceptMine: (inviteId) => post(`/corp/invites/mine/${inviteId}/accept`, {}),

  invites: (id) => apiJson(`${org(id)}/invites`),
  invite: (id, { emails, role, unitId, unitRole }) =>
    post(`${org(id)}/invites`, { emails, role, unit_id: unitId ?? null, unit_role: unitRole }),
  revokeInvite: (id, inviteId) => apiJson(`${org(id)}/invites/${inviteId}`, { method: 'DELETE' }),

  // ---- work memory (corporate/routes_work.py)
  threads: (id) => apiJson(`${org(id)}/threads`),
  direct: (id, userId) => post(`${org(id)}/threads/direct`, { user_id: userId }),
  messages: (id, t, after = 0) => apiJson(`${org(id)}/threads/${t}/messages?after=${after}`),
  send: (id, t, body) => post(`${org(id)}/threads/${t}/messages`, { body }),
  read: (id, t, upto) => post(`${org(id)}/threads/${t}/read`, { upto }),
  threadCommonGround: (id, t) => post(`${org(id)}/threads/${t}/common-ground`, {}),
  meetings: (id, unitId) => apiJson(`${org(id)}/meetings${unitId ? `?unit_id=${unitId}` : ''}`),
  meeting: (id, m) => apiJson(`${org(id)}/meetings/${m}`),
  createMeeting: (id, data) => (data instanceof FormData ? postForm(`${org(id)}/meetings`, data) : post(`${org(id)}/meetings`, data)),
  deleteMeeting: (id, m) => apiJson(`${org(id)}/meetings/${m}`, { method: 'DELETE' }),
  meetingCommonGround: (id, m) => post(`${org(id)}/meetings/${m}/common-ground`, {}),
  importable: (id) => apiJson(`${org(id)}/importable-conversations`),
  commitments: (id, scope, unitId) => apiJson(`${org(id)}/commitments?scope=${scope}${unitId ? `&unit_id=${unitId}` : ''}`),
  commitment: (id, c) => apiJson(`${org(id)}/commitments/${c}`),
  addCommitment: (id, data) => post(`${org(id)}/commitments`, data),
  commitmentAction: (id, c, action, data = {}) => post(`${org(id)}/commitments/${c}/${action}`, data),
  decisions: (id, unitId) => apiJson(`${org(id)}/decisions${unitId ? `?unit_id=${unitId}` : ''}`),
  decision: (id, d) => apiJson(`${org(id)}/decisions/${d}`),
  addDecision: (id, data) => post(`${org(id)}/decisions`, data),
  addVersion: (id, d, text) => post(`${org(id)}/decisions/${d}/versions`, { text }),
  decisionFromCommonGround: (id, g, text) => post(`${org(id)}/common-ground/${g}/decision`, { text }),
  ask: (id, question, unitId) => post(`${org(id)}/ask`, { question, unit_id: unitId ?? null }),
  brief: (id) => apiJson(`${org(id)}/brief`),
  workNote: (id) => apiJson(`${org(id)}/work-note`),
  saveWorkNote: (id, note, sharing) => apiJson(`${org(id)}/work-note`, { method: 'PUT', ...json({ note, sharing }) }),

  // ---- projects, contacts (stakeholder map), cross-team requests, updates
  projects: (id) => apiJson(`${org(id)}/projects`),
  createProject: (id, data) => post(`${org(id)}/projects`, data),
  project: (id, p) => apiJson(`${org(id)}/projects/${p}`),
  editProject: (id, p, data) => apiJson(`${org(id)}/projects/${p}`, { method: 'PATCH', ...json(data) }),
  deleteProject: (id, p) => apiJson(`${org(id)}/projects/${p}`, { method: 'DELETE' }),
  projectMember: (id, p, userId, role) => apiJson(`${org(id)}/projects/${p}/members/${userId}`, role
    ? { method: 'PUT', ...json({ role }) } : { method: 'DELETE' }),
  addContact: (id, p, data) => post(`${org(id)}/projects/${p}/contacts`, data),
  editContact: (id, p, c, data) => apiJson(`${org(id)}/projects/${p}/contacts/${c}`, { method: 'PATCH', ...json(data) }),
  deleteContact: (id, p, c) => apiJson(`${org(id)}/projects/${p}/contacts/${c}`, { method: 'DELETE' }),
  request: (id, data) => post(`${org(id)}/requests`, data),
  incoming: (id) => apiJson(`${org(id)}/requests/incoming`),
  acceptRequest: (id, c, data) => post(`${org(id)}/requests/${c}/accept`, data),
  declineRequest: (id, c, reason) => post(`${org(id)}/requests/${c}/decline`, { reason }),
  updates: (id, c) => apiJson(`${org(id)}/commitments/${c}/updates`),
  postUpdate: (id, c, kind, text) => post(`${org(id)}/commitments/${c}/updates`, { kind, text }),

  catchUp: (id, data) => post(`${org(id)}/catch-up`, data),

  // ---- tickets (PAY-12) and what's been said about them
  tickets: (id, params = {}) => apiJson(`${org(id)}/tickets?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ''))}`),
  createTicket: (id, data) => post(`${org(id)}/tickets`, data),
  ticket: (id, t) => apiJson(`${org(id)}/tickets/${t}`),
  editTicket: (id, t, data) => apiJson(`${org(id)}/tickets/${t}`, { method: 'PATCH', ...json(data) }),
  ticketPerson: (id, t, userId, role, add) => apiJson(`${org(id)}/tickets/${t}/people/${userId}${add ? '' : `?role=${role}`}`,
    add ? { method: 'PUT', ...json({ role }) } : { method: 'DELETE' }),
  ticketComment: (id, t, body) => post(`${org(id)}/tickets/${t}/comments`, { body }),
  tagTicket: (id, t, sourceType, sourceId, shared) => post(`${org(id)}/tickets/${t}/links`, { source_type: sourceType, source_id: sourceId, shared }),
  ticketSuggestion: (id, t, linkId, action) => post(`${org(id)}/tickets/${t}/links/${linkId}/${action}`, {}),
  untag: (id, t, linkId) => apiJson(`${org(id)}/tickets/${t}/links/${linkId}`, { method: 'DELETE' }),
  ticketBrief: (id, t) => post(`${org(id)}/tickets/${t}/brief`, {}),
  ticketsOn: (id, sourceType, sourceId) => apiJson(`${org(id)}/ticket-links?source_type=${sourceType}&source_id=${sourceId}`),
  setTicketKey: (id, p, key) => apiJson(`${org(id)}/projects/${p}/ticket-key`, { method: 'PUT', ...json({ key }) }),
  myEmails: (id) => apiJson(`${org(id)}/mail-items`),
  mailLink: (id) => apiJson(`${org(id)}/mail-link`),
  setMailLink: (id, enabled) => apiJson(`${org(id)}/mail-link`, { method: 'PUT', ...json({ enabled }) }),

  previewInvite: (token) => apiJson(`/corp/invites/${encodeURIComponent(token)}`),
  acceptInvite: (token) => post(`/corp/invites/${encodeURIComponent(token)}/accept`, {}),
};

// Names shown for a unit role, which depend on the unit's kind.
export const managerLabel = (kind) => (kind === 'team' ? 'Team lead' : 'Department head');
export const ORG_ROLE_LABELS = { owner: 'Owner', admin: 'Admin', member: 'Member' };

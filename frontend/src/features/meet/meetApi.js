// Throughline Meet -- web client for the backend's meet/ package (QR only:
// browsers can't do the Bluetooth touch, iPhone Safari especially).
import { api, apiJson, del, post } from '../../api.js';

export class MeetUnavailable extends Error {}

// With MEET_ENABLED off, /meet/* falls through to the SPA page (HTML, 200).
async function meetJson(path, options) {
  const res = await api(path, options);
  const data = await res.json().catch(() => {
    throw new MeetUnavailable();
  });
  if (!res.ok && !(data && data.status)) {
    throw Object.assign(new Error(data.error || data.message || 'Request failed'), { status: res.status, data });
  }
  return data;
}

export const meetApi = {
  myCard: () => meetJson('/meet/card'),
  saveCard: (card) => apiJson('/meet/card', { method: 'PUT', body: JSON.stringify(card) }),
  startQr: () => meetJson('/meet/qr', { method: 'POST' }),
  qrStatus: (token) => meetJson(`/meet/qr/${token}`),
  scanQr: (payload) => meetJson('/meet/qr/scan', { method: 'POST', body: JSON.stringify({ payload }) }),
  encounter: (id) => apiJson(`/meet/encounter/${id}`),
  confirm: (id, accept) => post(`/meet/encounter/${id}/confirm`, { accept }),
  addFriend: (id) => post(`/meet/encounter/${id}/add-friend`, {}),
  remove: (id) => del(`/meet/encounter/${id}`),
};

export const QR_PREFIX = 'throughline-meet:';

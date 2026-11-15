// Loads the user's organisations and the one currently open. Which one is
// open is remembered per device (a user can belong to several).
import { useCallback, useEffect, useState } from 'react';
import { corpApi, CorpUnavailable } from './corpApi.js';

const ORG_KEY = 'tl_corp_org';
const readOrgId = () => { try { return Number(localStorage.getItem(ORG_KEY)) || null; } catch { return null; } };
const writeOrgId = (id) => { try { localStorage.setItem(ORG_KEY, String(id)); } catch { /* ignore */ } };

export function useCorpOrg() {
  // status: loading | unavailable | error | none (no organisation yet) | ready
  const [status, setStatus] = useState('loading');
  const [orgs, setOrgs] = useState([]);
  const [suspended, setSuspended] = useState([]); // organisations where an admin paused my access
  const [canCreate, setCanCreate] = useState(true);
  const [data, setData] = useState(null); // {org, me, units}
  const [error, setError] = useState(null);

  const openOrg = useCallback(async (id) => {
    const d = await corpApi.getOrg(id);
    writeOrgId(id);
    setData(d);
    setStatus('ready');
    return d;
  }, []);

  const reload = useCallback(async (preferId) => {
    try {
      const me = await corpApi.me();
      const list = me.orgs.filter((o) => o.status !== 'suspended');
      setSuspended(me.orgs.filter((o) => o.status === 'suspended'));
      setCanCreate(me.can_create !== false);
      setOrgs(list);
      if (!list.length) { setData(null); setStatus('none'); return; }
      const want = preferId || readOrgId();
      const pick = list.find((o) => o.id === want) || list[0];
      await openOrg(pick.id);
    } catch (e) {
      setError(e);
      setStatus(e instanceof CorpUnavailable ? 'unavailable' : 'error');
    }
  }, [openOrg]);

  useEffect(() => { reload(); }, [reload]);

  return {
    status, error, orgs, data, reload, openOrg, suspended, canCreate,
    // Most write endpoints answer with the fresh organisation view.
    apply: (d) => { if (d && d.org) setData((prev) => ({ ...prev, ...d })); },
  };
}

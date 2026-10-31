import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import QRCode from 'qrcode';
import jsQR from 'jsqr';
import { MeetUnavailable, QR_PREFIX, meetApi } from './meetApi.js';
import './meet.css';

// Throughline Meet on the web: swap personality cards by QR code. Same
// backend flow as the phone app's Bluetooth touch -- after a scan, both
// people see "Share your card?", and cards appear only once both share.

const splitList = (text) => text.split(',').map((s) => s.trim()).filter(Boolean);

function Card({ card, person }) {
  return (
    <div className="meet-card">
      <div className="meet-card-head">
        <div className="meet-avatar" aria-hidden="true">
          {person?.photo ? <img src={person.photo} alt="" /> : (person?.name || '?').slice(0, 1).toUpperCase()}
        </div>
        <div>
          <div className="meet-card-name">{person?.name}</div>
          {card.headline && <div className="meet-card-headline">{card.headline}</div>}
        </div>
      </div>
      {card.about && <p className="meet-card-about">{card.about}</p>}
      {card.interests?.length > 0 && <Chips title="Into" items={card.interests} />}
      {card.likes?.length > 0 && <Chips title="Likes" items={card.likes} />}
    </div>
  );
}

function Chips({ title, items }) {
  return (
    <div className="meet-chips">
      <div className="meet-label">{title}</div>
      <div className="meet-chip-row">{items.map((i) => <span key={i} className="meet-chip">{i}</span>)}</div>
    </div>
  );
}

function CardEditor({ initial, me, onSaved }) {
  const [headline, setHeadline] = useState(initial.headline || '');
  const [about, setAbout] = useState(initial.about || '');
  const [interests, setInterests] = useState((initial.interests || []).join(', '));
  const [likes, setLikes] = useState((initial.likes || []).join(', '));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const card = { headline: headline.trim(), about: about.trim(), interests: splitList(interests), likes: splitList(likes), sharing: true };

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const r = await meetApi.saveCard(card);
      onSaved(r.card);
    } catch {
      setError("Couldn't save your card. Check your connection and try again.");
      setSaving(false);
    }
  };

  return (
    <form className="meet-editor" onSubmit={save}>
      <h2>Your Meet card</h2>
      <p className="meet-soft">This is what people see after you both tap Share. You choose everything on it.</p>
      <label htmlFor="meet-headline">Headline</label>
      <input id="meet-headline" maxLength={80} value={headline} onChange={(e) => setHeadline(e.target.value)} placeholder="e.g. Product designer, trekker" />
      <label htmlFor="meet-about">About you</label>
      <textarea id="meet-about" maxLength={280} rows={3} value={about} onChange={(e) => setAbout(e.target.value)} placeholder="A line or two about how you think, work or spend your time" />
      <label htmlFor="meet-interests">Interests <span className="meet-soft">(comma separated)</span></label>
      <input id="meet-interests" value={interests} onChange={(e) => setInterests(e.target.value)} />
      <label htmlFor="meet-likes">Things you like <span className="meet-soft">(comma separated)</span></label>
      <input id="meet-likes" value={likes} onChange={(e) => setLikes(e.target.value)} />
      <div className="meet-label" style={{ marginTop: 16 }}>Preview</div>
      <Card card={card} person={me} />
      {error && <p className="meet-error">{error}</p>}
      <button type="submit" className="meet-primary" disabled={saving}>{saving ? 'Saving…' : 'Save card'}</button>
    </form>
  );
}

function MyCode({ onMatched }) {
  const [img, setImg] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let poll, refresh;
    const fresh = async () => {
      try {
        const q = await meetApi.startQr();
        if (cancelled) return;
        setError(null);
        setImg(await QRCode.toDataURL(q.payload, { margin: 1, width: 480 }));
        clearInterval(poll);
        poll = setInterval(async () => {
          try {
            const s = await meetApi.qrStatus(q.token);
            if (!cancelled && s.status === 'scanned' && s.encounter) onMatched(s.encounter);
          } catch { /* keep polling */ }
        }, 1500);
        clearTimeout(refresh);
        refresh = setTimeout(fresh, Math.max(10, q.expires_in - 15) * 1000);
      } catch {
        if (!cancelled) setError("Couldn't make your code. Check your connection.");
      }
    };
    fresh();
    return () => { cancelled = true; clearInterval(poll); clearTimeout(refresh); };
  }, [onMatched]);

  if (error) return <p className="meet-error">{error}</p>;
  if (!img) return <div className="meet-spinner" aria-busy="true" />;
  return (
    <div className="meet-qr">
      <img src={img} alt="Your Meet code" width={240} height={240} />
      <h3>Let them scan this</h3>
      <p className="meet-soft">From Meet in their Throughline app or the web app. Nothing is shared until you both tap Share.</p>
    </div>
  );
}

function Scanner({ onMatched }) {
  const video = useRef(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);

  useEffect(() => {
    let stream, raf, stopped = false, busy = false;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const tick = async () => {
      if (stopped) return;
      const v = video.current;
      if (v && v.readyState >= 2 && !busy) {
        canvas.width = v.videoWidth;
        canvas.height = v.videoHeight;
        ctx.drawImage(v, 0, 0);
        const code = jsQR(ctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
        if (code?.data?.startsWith(QR_PREFIX)) {
          busy = true;
          try {
            const r = await meetApi.scanQr(code.data);
            if (r.status === 'matched' && r.encounter) { onMatched(r.encounter); return; }
            setNotice(r.error || "That code didn't work. Try again.");
          } catch {
            setNotice("Couldn't send that. Check your connection.");
          }
          setTimeout(() => { busy = false; }, 2000);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
        if (stopped) { stream.getTracks().forEach((t) => t.stop()); return; }
        video.current.srcObject = stream;
        await video.current.play();
        raf = requestAnimationFrame(tick);
      } catch {
        setError('Camera unavailable. Allow camera access for this site, or ask them to scan your code instead.');
      }
    })();
    return () => { stopped = true; cancelAnimationFrame(raf); stream?.getTracks().forEach((t) => t.stop()); };
  }, [onMatched]);

  if (error) return <p className="meet-error">{error}</p>;
  return (
    <div className="meet-scan">
      <div className="meet-scan-frame">
        <video ref={video} playsInline muted />
        <div className="meet-scan-box" aria-hidden="true" />
      </div>
      <p className="meet-soft">{notice || 'Point at the code on their screen'}</p>
    </div>
  );
}

export default function MeetPage() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState('loading'); // loading | unavailable | failed | edit | ready | confirm | shared | ended
  const [mine, setMine] = useState(null); // { saved, card, me }
  const [tab, setTab] = useState('show');
  const [enc, setEnc] = useState(null);
  const [encAt, setEncAt] = useState(0);
  const [message, setMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [, setTick] = useState(0);

  useEffect(() => {
    meetApi.myCard()
      .then((r) => { setMine(r); setPhase(r.saved && r.card.sharing ? 'ready' : 'edit'); })
      .catch((e) => setPhase(e instanceof MeetUnavailable ? 'unavailable' : 'failed'));
  }, []);

  const onMatched = useCallback((e) => {
    setEnc(e);
    setEncAt(Date.now());
    setPhase(e.status === 'shared' ? 'shared' : 'confirm');
  }, []);

  // While a meeting is open: follow the other side's answer, tick the countdown.
  useEffect(() => {
    if (!enc || enc.status !== 'pending') return undefined;
    const t = setInterval(async () => {
      setTick((n) => n + 1);
      try {
        const next = await meetApi.encounter(enc.id);
        setEnc(next);
        setEncAt(Date.now());
        if (next.status === 'shared') setPhase('shared');
        else if (next.status !== 'pending') {
          setMessage(next.status === 'expired' ? 'That took too long. Scan again to retry.'
            : next.status === 'declined' ? (next.i_confirmed ? `${next.other.name} chose not to share this time.` : 'Not shared.')
              : 'Sharing stopped.');
          setPhase('ended');
        }
      } catch { /* keep trying */ }
    }, 1000);
    return () => clearInterval(t);
  }, [enc]);

  const answer = async (accept) => {
    setBusy(true);
    try {
      const next = await meetApi.confirm(enc.id, accept);
      setEnc(next);
      setEncAt(Date.now());
      if (!accept) { setEnc(null); setPhase('ready'); } else if (next.status === 'shared') setPhase('shared');
    } finally { setBusy(false); }
  };

  const addFriend = async () => {
    setBusy(true);
    try { await meetApi.addFriend(enc.id); setMessage(`You and ${enc.other.name} are now friends.`); } catch { setMessage("Couldn't add them. Try again."); } finally { setBusy(false); }
  };

  const stopSharing = async () => {
    setBusy(true);
    try { await meetApi.remove(enc.id); setEnc(null); setMessage(null); setPhase('ready'); } finally { setBusy(false); }
  };

  const left = enc?.expires_in != null ? Math.max(0, enc.expires_in - Math.floor((Date.now() - encAt) / 1000)) : null;

  let body;
  if (phase === 'loading') body = <div className="meet-spinner" aria-busy="true" />;
  else if (phase === 'unavailable') body = <p className="meet-soft">Meet isn't switched on yet.</p>;
  else if (phase === 'failed') body = <p className="meet-error">Couldn't reach Throughline. Check your connection and reload.</p>;
  else if (phase === 'edit') {
    body = <CardEditor initial={mine.card} me={mine.me} onSaved={(card) => { setMine({ ...mine, saved: true, card }); setPhase('ready'); }} />;
  } else if (phase === 'ready') {
    body = (
      <>
        <div className="meet-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'show'} className={tab === 'show' ? 'on' : ''} onClick={() => setTab('show')}>My code</button>
          <button role="tab" aria-selected={tab === 'scan'} className={tab === 'scan' ? 'on' : ''} onClick={() => setTab('scan')}>Scan</button>
        </div>
        {tab === 'show' ? <MyCode onMatched={onMatched} /> : <Scanner onMatched={onMatched} />}
        <button className="meet-link" onClick={() => setPhase('edit')}>Edit my card</button>
      </>
    );
  } else if (phase === 'confirm') {
    body = (
      <div className="meet-confirm">
        <div className="meet-merge" aria-hidden="true">
          <div className="meet-avatar me">{(mine.me?.name || '?').slice(0, 1).toUpperCase()}</div>
          <div className="meet-avatar them">{(enc.other.name || '?').slice(0, 1).toUpperCase()}</div>
        </div>
        <h2>{enc.i_confirmed ? `Waiting for ${enc.other.name}…` : `You met ${enc.other.name}`}</h2>
        <p className="meet-soft">
          {enc.i_confirmed ? "You've shared. Their card appears as soon as they share too."
            : enc.they_confirmed ? `${enc.other.name} has shared their card. Share yours to see it.`
              : `Share your card with ${enc.other.name}? You'll see theirs once you both share.`}
        </p>
        {left != null && <p className={'meet-countdown' + (left <= 15 ? ' urgent' : '')}>{left > 0 ? `${left}s left to share` : 'Time is up'}</p>}
        {!enc.i_confirmed && (
          <>
            <button className="meet-primary" disabled={busy} onClick={() => answer(true)}>Share my card</button>
            <button className="meet-secondary" disabled={busy} onClick={() => answer(false)}>Not now</button>
          </>
        )}
      </div>
    );
  } else if (phase === 'shared') {
    body = (
      <div className="meet-shared">
        {enc.card ? <div className="meet-reveal"><Card card={enc.card} person={enc.other} /></div>
          : <p className="meet-soft">{enc.other.name} has switched their card off.</p>}
        {message && <p className="meet-soft">{message}</p>}
        <button className="meet-primary" disabled={busy} onClick={addFriend}>Add friend</button>
        <button className="meet-secondary" onClick={() => { setEnc(null); setMessage(null); setPhase('ready'); }}>Done</button>
        <button className="meet-danger" disabled={busy} onClick={stopSharing}>Stop sharing</button>
      </div>
    );
  } else {
    body = (
      <div className="meet-confirm">
        <h2>Not shared</h2>
        <p className="meet-soft">{message}</p>
        <button className="meet-primary" onClick={() => { setEnc(null); setPhase('ready'); }}>Back</button>
      </div>
    );
  }

  return (
    <div className="meet-page">
      <header className="meet-header">
        <button className="meet-back" onClick={() => navigate('/app/os')} aria-label="Back">←</button>
        <h1>Meet</h1>
      </header>
      <main className="meet-main">{body}</main>
    </div>
  );
}

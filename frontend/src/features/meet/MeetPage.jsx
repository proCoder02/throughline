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

const MAX_TRAITS = 4;
const FRONT_TRAITS = 3;
const LINK_HINTS = ['linkedin.com/in/…', 'instagram.com/…', 'Your website'];
// Same names and shades as the app (meet_style.dart).
const COLORS = { amber: '#D98E4A', coral: '#E0705A', teal: '#2E9C8F', indigo: '#5B6CD6', plum: '#9A5BB5', forest: '#4F8A4B' };

function colorFor(chosen, name = '') {
  if (COLORS[chosen]) return COLORS[chosen];
  const values = Object.values(COLORS);
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) & 0x7fffffff;
  return values[h % values.length];
}

function linkLabel(url) {
  let host = url;
  try { host = new URL(url).host; } catch { /* keep as typed */ }
  host = host.toLowerCase().replace(/^www\./, '');
  if (host.includes('linkedin.')) return 'LinkedIn';
  if (host.includes('instagram.')) return 'Instagram';
  if (host === 'x.com' || host.includes('twitter.')) return 'X';
  if (host.includes('github.')) return 'GitHub';
  return host || 'Link';
}

// Designed for the first 3 seconds: face, name, role, a one-line hook, top
// traits, what you share, "ask me about". The rest sits behind "More".
function Card({ card, person, inCommon = [], initiallyExpanded = false }) {
  const [open, setOpen] = useState(initiallyExpanded);
  const accent = colorFor(card.color, person?.name);
  const role = [card.headline, card.organization].filter(Boolean).join(' · ');
  const firstName = (person?.name || '').split(/\s+/)[0];
  const extraTraits = (card.traits || []).slice(FRONT_TRAITS);
  const hasMore = card.style || card.about || card.education || card.interests?.length || card.likes?.length || extraTraits.length;
  return (
    <div className="meet-card v3" style={{ '--card-accent': accent }}>
      <div className="meet-banner" />
      <div className="meet-face">
        <div className="meet-avatar big" aria-hidden="true">
          {person?.photo ? <img src={person.photo} alt="" /> : (person?.name || '?').slice(0, 1).toUpperCase()}
        </div>
        {card.links?.length > 0 && (
          <div className="meet-link-icons">
            {card.links.map((l) => (
              <a key={l} href={l} target="_blank" rel="noopener noreferrer" title={linkLabel(l)} aria-label={linkLabel(l)}>
                {linkLabel(l).slice(0, 2)}
              </a>
            ))}
          </div>
        )}
      </div>
      <div className="meet-card-body">
        <div className="meet-card-name">{person?.name}</div>
        {role && <div className="meet-card-role">{role}</div>}
        {card.city && <div className="meet-card-headline">📍 {card.city}</div>}
        {card.hook && <p className="meet-hook">{card.hook}</p>}
        {card.traits?.length > 0 && (
          <div className="meet-chip-row">
            {card.traits.slice(0, FRONT_TRAITS).map((t) => <span key={t} className="meet-trait">✦ {t}</span>)}
          </div>
        )}
        {inCommon.length > 0 && (
          <div className="meet-common"><span className="meet-soft">🤝 You both · </span><strong>{inCommon.join(', ')}</strong></div>
        )}
        {card.ask_me && <div className="meet-askme"><span className="meet-soft">💬 Ask me about </span><strong>{card.ask_me}</strong></div>}
        {hasMore && (
          <button type="button" className="meet-more" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Less' : `More about ${firstName}`} <span className={'meet-caret' + (open ? ' up' : '')}>⌄</span>
          </button>
        )}
        {hasMore && open && (
          <div className="meet-more-body">
            {card.style && <blockquote className="meet-style">“{card.style}”</blockquote>}
            {card.about && <p className="meet-card-about">{card.about}</p>}
            {card.education && <div className="meet-card-headline">🎓 {card.education}</div>}
            {extraTraits.length > 0 && <Chips title="Also" items={extraTraits} />}
            {card.interests?.length > 0 && <Chips title="Into" items={card.interests} />}
            {card.likes?.length > 0 && <Chips title="Likes" items={card.likes} />}
          </div>
        )}
      </div>
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

function CardEditor({ initial, me, traitOptions = [], colors = [], canSuggest = false, onSaved }) {
  const [f, setF] = useState({
    headline: initial.headline || '', organization: initial.organization || '', city: initial.city || '',
    education: initial.education || '', style: initial.style || '', about: initial.about || '',
    hook: initial.hook || '', ask_me: initial.ask_me || '',
    interests: (initial.interests || []).join(', '), likes: (initial.likes || []).join(', '),
  });
  const [traits, setTraits] = useState(initial.traits || []);
  const [color, setColor] = useState(initial.color || '');
  const [links, setLinks] = useState([0, 1, 2].map((i) => (initial.links || [])[i] || ''));
  const [custom, setCustom] = useState('');
  const [saving, setSaving] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [notice, setNotice] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const card = {
    headline: f.headline.trim(), organization: f.organization.trim(), city: f.city.trim(), education: f.education.trim(),
    style: f.style.trim(), about: f.about.trim(), hook: f.hook.trim(), ask_me: f.ask_me.trim(), color,
    traits, interests: splitList(f.interests), likes: splitList(f.likes),
    links: links.map((l) => l.trim()).filter(Boolean), sharing: true,
  };
  const options = [...new Set([...traitOptions, ...traits])];
  const colorNames = colors.length ? colors : Object.keys(COLORS);
  const effective = colorFor(color, me?.name);

  // A strong first impression, most important first.
  const checklist = [
    [!!me?.photo, 'Add a profile photo in Settings -- faces are what people remember'],
    [!!card.headline, 'Add your role, e.g. "Product designer"'],
    [!!card.hook, "Write a one-line hook -- it's the first thing people read"],
    [card.traits.length > 0, 'Pick a few traits that sound like you'],
    [!!card.ask_me, 'Add "Ask me about" so people know how to start talking'],
    [!!(card.organization || card.city), 'Add where you work or your city'],
    [card.interests.length > 0, "Add a few things you're into"],
    [card.links.length > 0, 'Add a link (LinkedIn, Instagram...) so people can stay in touch'],
  ];
  const done = checklist.filter(([ok]) => ok).length;
  const tip = checklist.find(([ok]) => !ok)?.[1];

  const toggle = (t) => {
    if (traits.includes(t)) setTraits(traits.filter((x) => x !== t));
    else if (traits.length < MAX_TRAITS) setTraits([...traits, t]);
    else setNotice('Pick up to 4 -- remove one first.');
  };
  const addCustom = () => {
    const t = custom.trim();
    if (t && !traits.some((x) => x.toLowerCase() === t.toLowerCase())) toggle(t);
    setCustom('');
  };
  const suggest = async () => {
    setSuggesting(true);
    setNotice(null);
    try {
      const s = await meetApi.suggest();
      if (s.traits?.length) setTraits(s.traits.slice(0, MAX_TRAITS));
      if (s.style) setF((prev) => ({ ...prev, style: s.style }));
      setNotice('Suggested from what Throughline knows about you. Change anything before saving.');
    } catch (e) {
      setNotice(e?.data?.error || "Couldn't make a suggestion right now. Try again in a moment.");
    } finally {
      setSuggesting(false);
    }
  };
  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setNotice(null);
    try {
      const r = await meetApi.saveCard(card);
      onSaved(r.card);
    } catch {
      setNotice("Couldn't save your card. Check your connection and try again.");
      setSaving(false);
    }
  };

  return (
    <form className="meet-editor" onSubmit={save}>
      <h2>Your Meet card</h2>
      <p className="meet-soft">This is what people see after you both tap Share. Everything is optional.</p>
      <div className="meet-meter" style={{ '--card-accent': effective }}>
        <div className="meet-meter-top">
          <strong>{done === checklist.length ? 'Your card makes a great first impression' : `Your card is ${Math.round((done / checklist.length) * 100)}% done`}</strong>
          <span className="meet-soft">{done}/{checklist.length}</span>
        </div>
        <div className="meet-meter-bar"><span style={{ width: `${(done / checklist.length) * 100}%` }} /></div>
        {tip && <p className="meet-soft">Next: {tip}</p>}
      </div>

      <h3>Who you are</h3>
      <label htmlFor="meet-headline">Role or headline</label>
      <input id="meet-headline" maxLength={80} value={f.headline} onChange={set('headline')} placeholder="e.g. Product designer" />
      <label htmlFor="meet-org">Company or organisation</label>
      <input id="meet-org" maxLength={80} value={f.organization} onChange={set('organization')} />
      <label htmlFor="meet-city">City</label>
      <input id="meet-city" maxLength={80} value={f.city} onChange={set('city')} />
      <label htmlFor="meet-edu">Education</label>
      <input id="meet-edu" maxLength={80} value={f.education} onChange={set('education')} placeholder="e.g. MBA, IIM Indore" />

      <h3>Your hook</h3>
      <p className="meet-soft">One line people read first -- what's memorable about you, not your job title.</p>
      <input id="meet-hook" aria-label="Hook" maxLength={90} value={f.hook} onChange={set('hook')} placeholder="e.g. Builds AI products; happiest on a mountain trail." />
      <label htmlFor="meet-askme">Ask me about</label>
      <input id="meet-askme" maxLength={80} value={f.ask_me} onChange={set('ask_me')} placeholder="e.g. trekking in Ladakh" />

      <h3>Personality</h3>
      <p className="meet-soft">Pick up to 4 traits that sound like you.</p>
      {canSuggest && (
        <button type="button" className="meet-secondary meet-suggest" onClick={suggest} disabled={suggesting}>
          {suggesting ? 'Thinking…' : '✨ Suggest from Throughline'}
        </button>
      )}
      <div className="meet-chip-row">
        {options.map((t) => (
          <button type="button" key={t} className={'meet-chip-toggle' + (traits.includes(t) ? ' on' : '')}
            aria-pressed={traits.includes(t)} onClick={() => toggle(t)}>{t}</button>
        ))}
      </div>
      <div className="meet-inline">
        <input id="meet-custom-trait" maxLength={24} value={custom} onChange={(e) => setCustom(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCustom(); } }} placeholder="Add your own trait" aria-label="Add your own trait" />
        <button type="button" className="meet-secondary" onClick={addCustom}>Add</button>
      </div>
      <label htmlFor="meet-style">How you communicate</label>
      <input id="meet-style" maxLength={160} value={f.style} onChange={set('style')} placeholder="e.g. I think out loud and like getting to the point." />
      <label htmlFor="meet-about">About you</label>
      <textarea id="meet-about" maxLength={280} rows={3} value={f.about} onChange={set('about')} placeholder="A line or two about what drives you" />

      <h3>What you're into</h3>
      <label htmlFor="meet-interests">Interests <span className="meet-soft">(comma separated)</span></label>
      <input id="meet-interests" value={f.interests} onChange={set('interests')} />
      <label htmlFor="meet-likes">Things you like <span className="meet-soft">(comma separated)</span></label>
      <input id="meet-likes" value={f.likes} onChange={set('likes')} />

      <h3>Stay in touch</h3>
      <p className="meet-soft">Optional. Only people you both share with can see these.</p>
      {links.map((l, i) => (
        <input key={i} id={`meet-link-${i}`} aria-label={`Link ${i + 1}`} type="url" inputMode="url" value={l} placeholder={LINK_HINTS[i]}
          onChange={(e) => setLinks(links.map((x, j) => (j === i ? e.target.value : x)))} />
      ))}

      <h3>Card colour</h3>
      <div className="meet-colors" role="radiogroup" aria-label="Card colour">
        {colorNames.map((n) => (
          <button type="button" key={n} role="radio" aria-checked={COLORS[n] === effective} aria-label={`${n} card colour`}
            className={'meet-color' + (COLORS[n] === effective ? ' on' : '')} style={{ background: COLORS[n] }}
            onClick={() => setColor(n)} />
        ))}
      </div>

      <div className="meet-label" style={{ marginTop: 16 }}>Preview</div>
      <Card card={card} person={me} initiallyExpanded />
      {notice && <p className="meet-soft" role="status">{notice}</p>}
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
    try { await meetApi.addFriend(enc.id); setMessage(`Connected -- ${enc.other.name} is now in your Friends.`); } catch { setMessage("Couldn't add them. Try again."); } finally { setBusy(false); }
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
    body = (
      <CardEditor initial={mine.card} me={mine.me} traitOptions={mine.trait_options} colors={mine.colors} canSuggest={mine.can_suggest}
        onSaved={(card) => { setMine({ ...mine, saved: true, card }); setPhase('ready'); }} />
    );
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
        {enc.card ? <div className="meet-reveal"><Card card={enc.card} person={enc.other} inCommon={enc.in_common || []} /></div>
          : <p className="meet-soft">{enc.other.name} has switched their card off.</p>}
        {message && <p className="meet-soft">{message}</p>}
        <button className="meet-primary" disabled={busy} onClick={addFriend}>Connect</button>
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

import { useEffect, useRef, useState } from 'react';
import { subscribeConsent, acceptConsent, getConsentStatus } from '../lib/consent.js';
import { useFocusTrap } from '../hooks/useFocusTrap.js';
import { showToast } from '../lib/notify.js';
import { MicIcon, ShieldIcon } from '../icons.jsx';

/// Mounted once in App.jsx. Renders:
///  - the recording notice, when a Listen/call start asks for it
///    (lib/consent.js ensureRecordingNotice), and
///  - a one-time Terms/Privacy agreement for accounts that haven't
///    accepted the current version (e.g. created before it existed).
export default function ConsentHost({ onLogout }) {
  const [request, setRequest] = useState(null); // {kind, resolve}
  const [termsNeeded, setTermsNeeded] = useState(false);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const cardRef = useRef(null);

  useEffect(() => subscribeConsent((r) => { setChecked(false); setError(''); setRequest(r); }), []);
  useEffect(() => {
    getConsentStatus(true).then((s) => setTermsNeeded(!!s?.terms?.needed)).catch(() => {});
  }, []);

  const showTerms = termsNeeded && !request;
  const open = !!request || showTerms;
  const cancel = () => {
    if (!request) return; // the Terms prompt has no cancel -- agree or log out
    request.resolve(false);
    setRequest(null);
  };
  useFocusTrap(open, cardRef, request ? cancel : undefined);

  if (!open) return null;

  const accept = async (kind) => {
    setBusy(true); setError('');
    try {
      await acceptConsent(kind);
      if (kind === 'terms') setTermsNeeded(false);
      else { request.resolve(true); setRequest(null); }
    } catch (e) {
      setError(e.message || 'Could not save that. Please try again.');
      if (kind !== 'terms') showToast('Could not save your answer -- please try again.', 'error');
    } finally {
      setBusy(false);
    }
  };

  if (request?.kind === 'recording_notice') {
    return (
      <div className="confirm-overlay" role="dialog" aria-modal="true" aria-labelledby="rec-notice-title">
        <div className="confirm-card consent-card" ref={cardRef}>
          <div className="consent-icon" aria-hidden="true"><MicIcon /></div>
          <div className="confirm-title" id="rec-notice-title">Before you record</div>
          <div className="confirm-message">
            <p>Throughline turns what's said into a transcript and notes in your account. That includes other people's
              voices -- on calls, everyone's voices.</p>
            <ul>
              <li><strong>Tell people</strong> before you record them, and get their consent wherever the law requires it.</li>
              <li>Don't record anyone secretly or where they expect privacy.</li>
              <li>Audio is deleted once it's transcribed; the transcript stays until you delete it.</li>
            </ul>
            <p><a href="/privacy" target="_blank" rel="noopener">How recordings are handled</a></p>
          </div>
          <label className="consent-check">
            <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
            <span>I'll tell people before I record them.</span>
          </label>
          {error && <div className="auth-error" role="alert">{error}</div>}
          <div className="confirm-actions">
            <button className="btn secondary" onClick={cancel} disabled={busy}>Cancel</button>
            <button className="btn" onClick={() => accept('recording_notice')} disabled={!checked || busy}>
              {busy ? 'Saving…' : 'Continue'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="confirm-overlay" role="dialog" aria-modal="true" aria-labelledby="terms-title">
      <div className="confirm-card consent-card" ref={cardRef}>
        <div className="consent-icon" aria-hidden="true"><ShieldIcon /></div>
        <div className="confirm-title" id="terms-title">Our Terms and Privacy Policy</div>
        <div className="confirm-message">
          <p>Please review how Throughline handles your data and the rules for using it -- including telling people
            before you record them.</p>
          <p>
            <a href="/terms" target="_blank" rel="noopener">Terms of Service</a>{' · '}
            <a href="/privacy" target="_blank" rel="noopener">Privacy Policy</a>
          </p>
        </div>
        <label className="consent-check">
          <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          <span>I agree to the Terms of Service and Privacy Policy.</span>
        </label>
        {error && <div className="auth-error" role="alert">{error}</div>}
        <div className="confirm-actions">
          <button className="btn secondary" onClick={onLogout} disabled={busy}>Log out</button>
          <button className="btn" onClick={() => accept('terms')} disabled={!checked || busy}>{busy ? 'Saving…' : 'Agree'}</button>
        </div>
      </div>
    </div>
  );
}

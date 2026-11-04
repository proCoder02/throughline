import { useEffect, useRef, useState } from 'react';
import { post } from '../api.js';
import { EyeIcon, EyeOffIcon } from '../icons.jsx';

function PasswordField({ id, value, onChange, autoComplete, label = 'Password' }) {
  const [show, setShow] = useState(false);
  return (
    <>
      <label className="auth-label" htmlFor={id}>{label}</label>
      <div className="auth-pw">
        <input
          id={id} className="field" type={show ? 'text' : 'password'} value={value}
          onChange={(e) => onChange(e.target.value)} autoComplete={autoComplete} required
        />
        <button
          type="button" className="auth-pw-toggle" onClick={() => setShow((s) => !s)}
          aria-label={show ? 'Hide password' : 'Show password'} aria-pressed={show}
        >
          {show ? <EyeOffIcon /> : <EyeIcon />}
        </button>
      </div>
    </>
  );
}

// mode: login | register | forgot | reset (reset = arrived from the emailed
// link, /app?reset_token=...).
export default function AuthScreen({ auth }) {
  const resetToken = useRef(new URLSearchParams(window.location.search).get('reset_token')).current;
  const [mode, setMode] = useState(resetToken ? 'reset' : 'login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [identifier, setIdentifier] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const firstField = useRef(null);

  useEffect(() => { firstField.current?.focus(); }, [mode]);

  const switchTo = (m) => { setMode(m); setError(''); setNotice(''); setPassword(''); };

  const dropResetToken = () => {
    const params = new URLSearchParams(window.location.search);
    params.delete('reset_token');
    const rest = params.toString();
    window.history.replaceState(null, '', window.location.pathname + (rest ? '?' + rest : ''));
  };

  const submit = async (e) => {
    e.preventDefault();
    setError(''); setNotice('');
    if (mode === 'login' || mode === 'register') {
      if (!username.trim() || !password) return setError('Enter your username and password.');
      if (mode === 'register' && !email.trim()) return setError('Enter your email -- reminders and password resets go there.');
      if (mode === 'register' && password.length < 6) return setError('Use at least 6 characters for your password.');
    }
    if (mode === 'forgot' && !identifier.trim()) return setError('Enter your username or email.');
    if (mode === 'reset' && password.length < 6) return setError('Use at least 6 characters for your new password.');
    setBusy(true);
    try {
      if (mode === 'register') await auth.register(username.trim(), email.trim(), password, 'personal');
      else if (mode === 'login') await auth.login(username.trim(), password);
      else if (mode === 'forgot') {
        const d = await post('/password/forgot', { identifier: identifier.trim() });
        setNotice(d.message);
      } else if (mode === 'reset') {
        const d = await post('/password/reset', { token: resetToken, password });
        dropResetToken();
        setMode('login'); setPassword('');
        setNotice(d.message);
      }
    } catch (err) {
      setError(err.message || 'Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  const titles = {
    login: 'Log in', register: 'Create your account', forgot: 'Reset your password', reset: 'Choose a new password',
  };
  const buttons = {
    login: busy ? 'Logging in…' : 'Log in', register: busy ? 'Creating…' : 'Create account',
    forgot: busy ? 'Sending…' : 'Email me a reset link', reset: busy ? 'Saving…' : 'Save new password',
  };

  return (
    <main className="auth-screen">
      <div className="auth-card">
        <h1>Throughline</h1>
        <p className="hint">Listens once. Remembers everything.</p>
        <h2 style={{ fontSize: 16, margin: '0 0 4px' }}>{titles[mode]}</h2>

        <form onSubmit={submit} noValidate>
          {(mode === 'login' || mode === 'register') && (
            <>
              <label className="auth-label" htmlFor="auth-username">Username</label>
              <input
                id="auth-username" ref={firstField} className="field" value={username} required
                onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false}
              />
            </>
          )}
          {mode === 'register' && (
            <>
              <label className="auth-label" htmlFor="auth-email">Email</label>
              <input
                id="auth-email" className="field" type="email" value={email} required
                onChange={(e) => setEmail(e.target.value)} autoComplete="email"
              />
              <div className="auth-field-hint">For task reminders and password resets.</div>
            </>
          )}
          {(mode === 'login' || mode === 'register') && (
            <PasswordField
              id="auth-password" value={password} onChange={setPassword}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
          )}
          {mode === 'register' && <div className="auth-field-hint">At least 6 characters.</div>}
          {mode === 'login' && (
            <div className="auth-row">
              <button type="button" className="link-button" onClick={() => switchTo('forgot')}>Forgot password?</button>
            </div>
          )}

          {mode === 'forgot' && (
            <>
              <p className="auth-field-hint" style={{ marginTop: 0 }}>
                Enter your username or email. We'll email you a link to choose a new password.
              </p>
              <label className="auth-label" htmlFor="auth-identifier">Username or email</label>
              <input
                id="auth-identifier" ref={firstField} className="field" value={identifier} required
                onChange={(e) => setIdentifier(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false}
              />
            </>
          )}

          {mode === 'reset' && (
            <>
              <PasswordField id="auth-new-password" label="New password" value={password} onChange={setPassword} autoComplete="new-password" />
              <div className="auth-field-hint">At least 6 characters.</div>
            </>
          )}

          <button type="submit" className="btn" style={{ width: '100%', marginTop: 16 }} disabled={busy}>
            {buttons[mode]}
          </button>
        </form>

        <div className="auth-error" role="alert">{error}</div>
        {notice && <div className="auth-success" role="status">{notice}</div>}

        <div className="auth-switch">
          {mode === 'login' && <>New to Throughline? <button type="button" className="link-button" onClick={() => switchTo('register')}>Create an account</button></>}
          {mode === 'register' && <>Already have an account? <button type="button" className="link-button" onClick={() => switchTo('login')}>Log in</button></>}
          {(mode === 'forgot' || mode === 'reset') && (
            <button type="button" className="link-button" onClick={() => { if (mode === 'reset') dropResetToken(); switchTo('login'); }}>Back to log in</button>
          )}
        </div>
      </div>
    </main>
  );
}

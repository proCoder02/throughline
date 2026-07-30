import { useState } from 'react';

export default function AuthScreen({ auth }) {
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!username || !password) return setError('Enter a username and password.');
    if (mode === 'register' && !email) return setError('Enter an email -- used for reminder emails.');
    setBusy(true);
    setError('');
    try {
      if (mode === 'register') await auth.register(username, email, password, 'personal');
      else await auth.login(username, password);
    } catch (e) {
      setError(e.message || 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <h1>Throughline</h1>
        <p className="hint">Listens once. Remembers everything.</p>
        <input className="field" placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} />
        {mode === 'register' && (
          <input className="field" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
        )}
        <input
          className="field" type="password" placeholder="Password" value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
        />
        <button className="btn" style={{ width: '100%', marginTop: 16 }} disabled={busy} onClick={submit}>
          {mode === 'login' ? 'Log in' : 'Create account'}
        </button>
        <div className="auth-error">{error}</div>
        <div className="auth-switch" onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(''); }}>
          {mode === 'login' ? "Need an account? Register" : 'Already have an account? Log in'}
        </div>
      </div>
    </div>
  );
}

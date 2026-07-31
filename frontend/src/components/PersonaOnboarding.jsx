import { useEffect, useState } from 'react';
import { apiJson, post } from '../api.js';

export default function PersonaOnboarding({ onComplete }) {
  const [options, setOptions] = useState(null);
  const [age, setAge] = useState('');
  const [gender, setGender] = useState('');
  const [language, setLanguage] = useState('');
  const [hobbies, setHobbies] = useState([]);
  const [interests, setInterests] = useState([]);
  const [occupation, setOccupation] = useState('');
  const [primaryGoal, setPrimaryGoal] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiJson('/persona')
      .then((d) => setOptions(d.options))
      .catch(() => setOptions({ gender: [], language_preference: [], hobbies: [], interests: [] }));
  }, []);

  const toggle = (list, setList, value) => {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  };

  const submit = async () => {
    setError('');
    setBusy(true);
    try {
      await post('/persona', {
        age, gender, language_preference: language, hobbies, interests,
        occupation, primary_goal: primaryGoal,
      });
      onComplete();
    } catch (e) {
      setError(e.message || 'Something went wrong.');
    } finally {
      setBusy(false);
    }
  };

  if (!options) return null;

  return (
    <div className="auth-screen">
      <div className="auth-card persona-card">
        <h1>Tell us a bit about you</h1>
        <p className="hint">
          We use this information to make your experience more personalized and better tailored to you.
        </p>

        <label className="hint">Age</label>
        <input className="field" type="number" min="13" max="120" value={age} onChange={(e) => setAge(e.target.value)} />

        <label className="hint">Gender</label>
        <select className="field" value={gender} onChange={(e) => setGender(e.target.value)}>
          <option value="" disabled>Select...</option>
          {options.gender.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>

        <label className="hint">Language preference</label>
        <select className="field" value={language} onChange={(e) => setLanguage(e.target.value)}>
          <option value="" disabled>Select...</option>
          {options.language_preference.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>

        <label className="hint">Hobbies (pick at least one)</label>
        <div className="persona-options">
          {options.hobbies.map((h) => (
            <label key={h} className={'persona-chip' + (hobbies.includes(h) ? ' active' : '')}>
              <input type="checkbox" checked={hobbies.includes(h)} onChange={() => toggle(hobbies, setHobbies, h)} />
              {h}
            </label>
          ))}
        </div>

        <label className="hint">Interests (pick at least one)</label>
        <div className="persona-options">
          {options.interests.map((i) => (
            <label key={i} className={'persona-chip' + (interests.includes(i) ? ' active' : '')}>
              <input type="checkbox" checked={interests.includes(i)} onChange={() => toggle(interests, setInterests, i)} />
              {i}
            </label>
          ))}
        </div>

        <label className="hint">Occupation</label>
        <input className="field" placeholder="e.g. Software Engineer" value={occupation} onChange={(e) => setOccupation(e.target.value)} />

        <label className="hint">What brings you here? (optional)</label>
        <input className="field" placeholder="e.g. stay organized, understand people better" value={primaryGoal} onChange={(e) => setPrimaryGoal(e.target.value)} />

        <button className="btn" style={{ width: '100%', marginTop: 16 }} disabled={busy} onClick={submit}>
          Continue
        </button>
        <div className="auth-error">{error}</div>
      </div>
    </div>
  );
}

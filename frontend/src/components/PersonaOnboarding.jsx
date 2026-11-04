import { useEffect, useState } from 'react';
import { apiJson, post } from '../api.js';

export default function PersonaOnboarding({ onComplete, onSkip }) {
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
      <div className="auth-card persona-card" role="dialog" aria-modal="true" aria-labelledby="persona-title">
        <h1 id="persona-title" style={{ fontSize: 24 }}>Personalize Throughline</h1>
        <p className="persona-why">
          A few answers help Throughline word its replies, reminders and insights for you. Takes about a minute --
          you can skip it and come back from Home any time.
        </p>

        <label className="auth-label" htmlFor="persona-age">Age</label>
        <input id="persona-age" className="field" type="number" min="13" max="120" value={age} onChange={(e) => setAge(e.target.value)} />

        <label className="auth-label" htmlFor="persona-gender">Gender</label>
        <select id="persona-gender" className="field" value={gender} onChange={(e) => setGender(e.target.value)}>
          <option value="" disabled>Select...</option>
          {options.gender.map((g) => <option key={g} value={g}>{g}</option>)}
        </select>

        <label className="auth-label" htmlFor="persona-language">Language preference</label>
        <select id="persona-language" className="field" value={language} onChange={(e) => setLanguage(e.target.value)}>
          <option value="" disabled>Select...</option>
          {options.language_preference.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>

        <div className="auth-label" id="persona-hobbies">Hobbies <span className="auth-field-hint">(pick at least one)</span></div>
        <div className="persona-options" role="group" aria-labelledby="persona-hobbies">
          {options.hobbies.map((h) => (
            <label key={h} className={'persona-chip' + (hobbies.includes(h) ? ' active' : '')}>
              <input type="checkbox" checked={hobbies.includes(h)} onChange={() => toggle(hobbies, setHobbies, h)} />
              {h}
            </label>
          ))}
        </div>

        <div className="auth-label" id="persona-interests">Interests <span className="auth-field-hint">(pick at least one)</span></div>
        <div className="persona-options" role="group" aria-labelledby="persona-interests">
          {options.interests.map((i) => (
            <label key={i} className={'persona-chip' + (interests.includes(i) ? ' active' : '')}>
              <input type="checkbox" checked={interests.includes(i)} onChange={() => toggle(interests, setInterests, i)} />
              {i}
            </label>
          ))}
        </div>

        <label className="auth-label" htmlFor="persona-occupation">Occupation</label>
        <input id="persona-occupation" className="field" placeholder="e.g. Software Engineer" value={occupation} onChange={(e) => setOccupation(e.target.value)} />

        <label className="auth-label" htmlFor="persona-goal">What brings you here? <span className="auth-field-hint">(optional)</span></label>
        <input id="persona-goal" className="field" placeholder="e.g. stay organized, understand people better" value={primaryGoal} onChange={(e) => setPrimaryGoal(e.target.value)} />

        <div className="persona-actions">
          {onSkip && <button type="button" className="btn secondary" disabled={busy} onClick={onSkip}>Skip for now</button>}
          <button type="button" className="btn" disabled={busy} onClick={submit}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
        <div className="auth-error" role="alert">{error}</div>
      </div>
    </div>
  );
}

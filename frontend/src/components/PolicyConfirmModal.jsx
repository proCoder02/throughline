import { useState } from 'react';
import { ShieldIcon } from '../icons.jsx';

const COVERAGE_LABELS = {
  comprehensive: 'Comprehensive',
  own_damage: 'Own Damage',
  third_party: 'Third Party',
};

// Web counterpart of the Flutter client's showPolicyConfirmSheet
// (policy_confirm_sheet.dart) -- shown right after a vehicle insurance
// policy is extracted from an uploaded photo or PDF (see ChatsSection's
// sendGlobalImageMessage/sendGlobalPdfMessage). Lets the user fix a misread
// field and always asks for the claims email (almost never printed on the
// document itself) before POST /vehicle-policies actually saves it.
export default function PolicyConfirmModal({ policy, onSave, onCancel }) {
  const [label, setLabel] = useState(policy.label || '');
  const [insurerName, setInsurerName] = useState(policy.insurer_name || '');
  const [policyNumber, setPolicyNumber] = useState(policy.policy_number || '');
  const [vehicleRegistration, setVehicleRegistration] = useState(policy.vehicle_registration || '');
  const [issueDate, setIssueDate] = useState(policy.issue_date || '');
  const [expiryDate, setExpiryDate] = useState(policy.expiry_date || '');
  const [sumInsured, setSumInsured] = useState(policy.sum_insured != null ? String(policy.sum_insured) : '');
  const [claimsEmail, setClaimsEmail] = useState(policy.claims_email || '');
  const [coverageType, setCoverageType] = useState(policy.coverage_type || 'comprehensive');

  const canSave = insurerName.trim() && policyNumber.trim() && claimsEmail.trim();

  const save = () => {
    if (!canSave) return;
    const sum = parseFloat(sumInsured);
    onSave({
      label: label.trim() || null,
      insurer_name: insurerName.trim(),
      policy_number: policyNumber.trim(),
      vehicle_registration: vehicleRegistration.trim() || null,
      issue_date: issueDate.trim() || null,
      expiry_date: expiryDate.trim() || null,
      sum_insured: Number.isNaN(sum) ? null : sum,
      claims_email: claimsEmail.trim(),
      coverage_type: coverageType,
    });
  };

  return (
    <div className="confirm-overlay" onClick={onCancel}>
      <div className="confirm-card" style={{ textAlign: 'left', maxHeight: '85vh', overflowY: 'auto' }} onClick={(e) => e.stopPropagation()}>
        <div className="confirm-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ShieldIcon /> Save this policy?
        </div>
        <div className="confirm-message">Check the details, and add the insurer's claims email -- it's rarely on the document itself.</div>

        <label className="hint" style={{ margin: '14px 0 6px' }}>Coverage</label>
        <div className="row" style={{ gap: 6 }}>
          {Object.entries(COVERAGE_LABELS).map(([value, text]) => (
            <button
              key={value}
              type="button"
              className={'btn small' + (coverageType === value ? '' : ' secondary')}
              onClick={() => setCoverageType(value)}
            >
              {text}
            </button>
          ))}
        </div>

        <label className="hint" style={{ margin: '10px 0 0' }}>Label (optional)</label>
        <input className="field" placeholder="e.g. Bike, Activa" autoFocus value={label} onChange={(e) => setLabel(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Insurer</label>
        <input className="field" value={insurerName} onChange={(e) => setInsurerName(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Policy number</label>
        <input className="field" value={policyNumber} onChange={(e) => setPolicyNumber(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Vehicle registration (optional)</label>
        <input className="field" value={vehicleRegistration} onChange={(e) => setVehicleRegistration(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Issue date (optional)</label>
        <input className="field" placeholder="YYYY-MM-DD" value={issueDate} onChange={(e) => setIssueDate(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Expiry date (optional)</label>
        <input className="field" placeholder="YYYY-MM-DD" value={expiryDate} onChange={(e) => setExpiryDate(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Sum insured (optional)</label>
        <input className="field" type="number" step="1" placeholder="Insured declared value" value={sumInsured} onChange={(e) => setSumInsured(e.target.value)} />

        <label className="hint" style={{ margin: '10px 0 0' }}>Claims email</label>
        <input className="field" type="email" placeholder="e.g. claims@insurer.com" value={claimsEmail} onChange={(e) => setClaimsEmail(e.target.value)} />

        <div className="confirm-actions">
          <button className="btn secondary" onClick={onCancel}>Discard</button>
          <button className="btn" disabled={!canSave} onClick={save}>Save</button>
        </div>
      </div>
    </div>
  );
}

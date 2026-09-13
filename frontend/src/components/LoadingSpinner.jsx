import UseAnimations from 'react-useanimations';
import loading2 from 'react-useanimations/lib/loading2';

/// Small inline Lottie spinner (react-useanimations) -- drop-in replacement
/// for a plain "Loading..." text wherever a section is waiting on a fetch.
export default function LoadingSpinner({ size = 22, label }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <UseAnimations animation={loading2} size={size} autoplay loop strokeColor="#1FC8B4" />
      {label && <span className="hint" style={{ margin: 0 }}>{label}</span>}
    </span>
  );
}

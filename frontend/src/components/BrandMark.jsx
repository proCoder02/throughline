// The Throughline mark: a thread joining two points. One drawing for every
// logo -- the homepage and entry-screen header (BrandShell, on its amber
// tile) and the app sidebars (inside their own amber tile, so only the
// thread is drawn here, in currentColor).
export default function BrandMark({ size = 22, tile = false }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" focusable="false">
      {tile && <rect width="32" height="32" rx="9" fill="#A85F24" />}
      <path d="M6 21c5 0 6-9 11-9s5 6 9 6" fill="none" stroke={tile ? '#fff' : 'currentColor'} strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="6" cy="21" r="2.4" fill={tile ? '#fff' : 'currentColor'} />
      <circle cx="26" cy="18" r="2.4" fill={tile ? '#fff' : 'currentColor'} />
    </svg>
  );
}

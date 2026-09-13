/// Single render point for every avatar in the app -- a real profile
/// picture (<img>, object-fit: cover via .avatar's own CSS) when `url` is
/// set, the initial-letter circle otherwise. Every existing
/// `<span className="avatar">{x[0]}</span>` usage is a candidate to
/// migrate to this component so a profile picture actually shows up
/// everywhere at once instead of in one place at a time.
export default function Avatar({ url, name, size, className = '', style }) {
  const sizeClass = size === 'lg' ? 'lg' : size === 'xl' ? 'xl' : '';
  const cls = ['avatar', sizeClass, className].filter(Boolean).join(' ');
  if (url) {
    return <img src={url} alt="" className={cls} style={style} />;
  }
  return <span className={cls} style={style}>{(name || '?')[0]}</span>;
}

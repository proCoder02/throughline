// The brand frame shared by the homepage and the entry screens (log in,
// sign up, the Personal/Corporate chooser, first-run setup): the same logo,
// header and footer everywhere, so moving from the homepage into the app
// never looks like a different site. Styles: styles/brand.css.
import '../styles/brand.css';

export function BrandLogo({ href = '/' }) {
  return (
    <a className="br-logo" href={href} aria-label="Throughline home">
      <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
        <rect width="32" height="32" rx="9" fill="#A85F24" />
        <path d="M6 21c5 0 6-9 11-9s5 6 9 6" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
        <circle cx="6" cy="21" r="2.4" fill="#fff" />
        <circle cx="26" cy="18" r="2.4" fill="#fff" />
      </svg>
      <span>Throughline</span>
    </a>
  );
}

export function BrandFooter() {
  return (
    <footer className="br-footer">
      <div className="br-footer-row">
        <BrandLogo />
        <nav className="br-footer-links" aria-label="Legal">
          <a href="/">Home</a>
          <a href="/privacy">Privacy</a>
          <a href="/terms">Terms</a>
        </nav>
        <p className="br-footer-copy">© {new Date().getFullYear()} Throughline</p>
      </div>
    </footer>
  );
}

// headerLink: optional { href, label } shown on the right of the header.
export default function BrandShell({ children, headerLink, className = '', logoHref = '/' }) {
  return (
    <div className={`br-page ${className}`.trim()}>
      <header className="br-header">
        <div className="br-header-row">
          <BrandLogo href={logoHref} />
          {headerLink && <a className="br-header-link" href={headerLink.href}>{headerLink.label}</a>}
        </div>
      </header>
      <div className="br-main">{children}</div>
      <BrandFooter />
    </div>
  );
}

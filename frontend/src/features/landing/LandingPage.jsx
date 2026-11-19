// Public homepage at "/": what Throughline is, how it works, Personal and
// Work, pricing (beta + pilot), and the way in. Everything the app itself
// does stays under /app; the buttons here hand off to its login/sign-up.
import { useEffect } from 'react';
import { getToken } from '../../api.js';
import './landing.css';

const SIGNUP = '/app?signup=1';
const LOGIN = '/app';
const PILOT = '/app/corp?signup=1';

function Logo() {
  return (
    <a className="lp-logo" href="/" aria-label="Throughline home">
      <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
        <rect width="32" height="32" rx="9" fill="var(--lp-accent)" />
        <path d="M6 21c5 0 6-9 11-9s5 6 9 6" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
        <circle cx="6" cy="21" r="2.4" fill="#fff" />
        <circle cx="26" cy="18" r="2.4" fill="#fff" />
      </svg>
      <span>Throughline</span>
    </a>
  );
}

function Check() {
  return (
    <svg className="lp-check" viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
      <path d="M4 10.5l4 4 8-9" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// A small, static picture of the product's core loop: said on a call ->
// picked out as a promise -> reminded -> closed by later evidence.
function HeroDemo() {
  return (
    <div className="lp-demo" role="img" aria-label="Example: a promise made on a call is picked out, reminded on Friday, and closed when the email goes out">
      <div className="lp-demo-card">
        <div className="lp-demo-meta"><span className="lp-dot lp-dot-live" />Call with Priya · Tue 2:14 PM</div>
        <p className="lp-demo-quote">“I’ll send the revised quote by Friday.”</p>
      </div>
      <div className="lp-demo-link" aria-hidden="true" />
      <div className="lp-demo-card lp-demo-promise">
        <div className="lp-demo-meta">Promise picked out</div>
        <p className="lp-demo-title">Send Priya the revised quote</p>
        <div className="lp-chips"><span className="lp-chip">You → Priya</span><span className="lp-chip">Due Fri</span><span className="lp-chip">From the call</span></div>
      </div>
      <div className="lp-demo-link" aria-hidden="true" />
      <div className="lp-demo-card lp-demo-done">
        <div className="lp-demo-meta"><span className="lp-dot lp-dot-done" />Done · Thu 6:40 PM</div>
        <p className="lp-demo-title">Closed by your email “Revised quote attached”</p>
      </div>
    </div>
  );
}

const STEPS = [
  { n: '1', title: 'Capture', body: 'Calls and meetings are transcribed with who said what. Team chat and, if you choose, Gmail or Outlook join in.' },
  { n: '2', title: 'Understand', body: 'AI picks out promises, owners and due dates, decisions and blockers, and links ticket numbers like PAY-12.' },
  { n: '3', title: 'Follow through', body: 'Reminders before things slip. A promise closes when a later message shows it’s done. Ask questions anytime.' },
];

const PERSONAL = [
  { title: 'Record and transcribe', body: 'Calls and in-person conversations, with who said what.' },
  { title: 'Ask Throughline', body: 'Questions answered from your own conversations, tasks and email.' },
  { title: 'Promises and tasks', body: 'Pulled out automatically, with reminders before they’re due.' },
  { title: 'People', body: 'What you discussed with each person, and what you owe them.' },
  { title: 'Meet', body: 'Built-in video calls, transcribed as you talk.' },
  { title: 'Your mailbox', body: 'Gmail and Outlook, read-only and only if you connect them.' },
];

const PLANS = [
  {
    name: 'Personal', price: 'Free', note: 'during beta', cta: 'Get started', href: SIGNUP, featured: false,
    points: ['Record and transcribe calls', 'Ask Throughline about your life and email', 'Promises and reminders', 'People and Meet', 'Web and Android'],
  },
  {
    name: 'Work', price: 'Free pilot', note: 'for early teams, then per seat', cta: 'Start a pilot', href: PILOT, featured: true,
    points: ['Teams, reporting lines and roles', 'Commitments from chat, meetings and email', 'Tickets with the full conversation trail', 'Ask, limited to what each person may see', 'Client view and weekly digest for Sales'],
  },
  {
    name: 'Enterprise', price: 'Talk to us', note: 'for larger organisations', cta: 'Start with a pilot', href: PILOT, featured: false,
    points: ['Everything in Work', 'Company sign-in with Microsoft or Google (coming soon)', 'Custom retention and data terms', 'Guided onboarding'],
  },
];

export default function LandingPage() {
  const signedIn = Boolean(getToken());

  useEffect(() => {
    const before = document.title;
    document.title = 'Throughline · Every promise, followed through';
    return () => { document.title = before; };
  }, []);

  return (
    <div className="lp">
      <a className="lp-skip" href="#main">Skip to content</a>
      <header className="lp-nav">
        <div className="lp-wrap lp-nav-row">
          <Logo />
          <nav className="lp-nav-links" aria-label="Sections">
            <a href="#how">How it works</a>
            <a href="#personal">Personal</a>
            <a href="#work">Work</a>
            <a href="#pricing">Pricing</a>
          </nav>
          <div className="lp-nav-cta">
            {signedIn ? (
              <a className="lp-btn lp-btn-primary" href={LOGIN}>Open Throughline</a>
            ) : (
              <>
                <a className="lp-btn lp-btn-ghost" href={LOGIN}>Log in</a>
                <a className="lp-btn lp-btn-primary" href={SIGNUP}>Get started</a>
              </>
            )}
          </div>
        </div>
      </header>

      <main id="main">
        <section className="lp-hero">
          <div className="lp-wrap lp-hero-grid">
            <div className="lp-hero-text">
              <p className="lp-eyebrow">Now in beta · Web and Android</p>
              <h1>Every promise, followed through.</h1>
              <p className="lp-lede">
                Throughline remembers your conversations across calls, chat and email, picks out what was promised,
                and keeps it moving until it’s done. For you, and for your whole team.
              </p>
              <div className="lp-hero-cta">
                {signedIn ? (
                  <a className="lp-btn lp-btn-primary lp-btn-lg" href={LOGIN}>Open Throughline</a>
                ) : (
                  <>
                    <a className="lp-btn lp-btn-primary lp-btn-lg" href={SIGNUP}>Get started free</a>
                    <a className="lp-btn lp-btn-ghost lp-btn-lg" href={LOGIN}>Log in</a>
                  </>
                )}
              </div>
              <p className="lp-fine">Free during beta. No card needed.</p>
            </div>
            <HeroDemo />
          </div>
        </section>

        <section className="lp-section lp-problem" aria-labelledby="problem-h">
          <div className="lp-wrap">
            <p className="lp-eyebrow">The problem</p>
            <h2 id="problem-h">Work runs on promises, and most are made in conversation</h2>
            <div className="lp-grid3">
              <article className="lp-card">
                <h3>Said, then lost</h3>
                <p>“I’ll send it by Friday,” said on a call, lives only in two people’s memories.</p>
              </article>
              <article className="lp-card">
                <h3>Split across channels</h3>
                <p>The call, the chat and the email thread each hold one piece. Ticketing tools only hold what someone typed in.</p>
              </article>
              <article className="lp-card">
                <h3>Status means chasing</h3>
                <p>Sales and business heads ask engineering for updates the client already asked them for.</p>
              </article>
            </div>
          </div>
        </section>

        <section id="how" className="lp-section lp-alt" aria-labelledby="how-h">
          <div className="lp-wrap">
            <p className="lp-eyebrow">How it works</p>
            <h2 id="how-h">Capture, understand, follow through</h2>
            <ol className="lp-steps">
              {STEPS.map((s) => (
                <li key={s.n} className="lp-step">
                  <span className="lp-step-n" aria-hidden="true">{s.n}</span>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="personal" className="lp-section" aria-labelledby="personal-h">
          <div className="lp-wrap">
            <p className="lp-eyebrow">For you</p>
            <h2 id="personal-h">A memory of your conversations</h2>
            <p className="lp-sub">Your personal side stays yours. It is never visible to an employer.</p>
            <div className="lp-grid3">
              {PERSONAL.map((f) => (
                <article key={f.title} className="lp-card">
                  <h3>{f.title}</h3>
                  <p>{f.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="work" className="lp-section lp-dark" aria-labelledby="work-h">
          <div className="lp-wrap">
            <p className="lp-eyebrow">Throughline Work</p>
            <h2 id="work-h">The same habit, for the whole company</h2>
            <div className="lp-grid2">
              <article className="lp-card">
                <p className="lp-tag">Admins, on the web</p>
                <ul className="lp-list">
                  <li>Departments, teams and reporting lines</li>
                  <li>Invitations, with a separate work identity for each company</li>
                  <li>Projects and deliverables, with role-based access</li>
                </ul>
              </article>
              <article className="lp-card">
                <p className="lp-tag">Employees, on the phone</p>
                <ul className="lp-list">
                  <li>Team chat and meetings become commitments</li>
                  <li>Tickets that carry the trail of calls, chat and email</li>
                  <li>Ask, answered only from what you’re allowed to see</li>
                </ul>
              </article>
            </div>
            <div className="lp-band">
              <h3>A client view for Sales and business heads</h3>
              <p>Deliverable status and risks, a weekly digest, and client update drafts written from engineering’s real progress.</p>
            </div>
          </div>
        </section>

        <section className="lp-section" aria-labelledby="why-h">
          <div className="lp-wrap">
            <p className="lp-eyebrow">Why it’s different</p>
            <h2 id="why-h">Note-taking stops at the meeting. Throughline keeps going.</h2>
            <div className="lp-table-wrap">
              <table className="lp-table">
                <thead>
                  <tr><th scope="col"><span className="lp-sr">Feature</span></th><th scope="col">Meeting note-takers</th><th scope="col">Work trackers</th><th scope="col" className="lp-us">Throughline</th></tr>
                </thead>
                <tbody>
                  <tr><th scope="row">Captures from</th><td data-label="Meeting note-takers">Meetings</td><td data-label="Work trackers">What people type in</td><td data-label="Throughline" className="lp-us">Calls, meetings, chat and email</td></tr>
                  <tr><th scope="row">Promises tracked to done</th><td data-label="Meeting note-takers">No, notes only</td><td data-label="Work trackers">Only if someone files a ticket</td><td data-label="Throughline" className="lp-us">Automatically, with owner and due date</td></tr>
                  <tr><th scope="row">Proof it was done</th><td data-label="Meeting note-takers">None</td><td data-label="Work trackers">Status set by hand</td><td data-label="Throughline" className="lp-us">Closed when a later message shows it</td></tr>
                  <tr><th scope="row">Status for the client side</th><td data-label="Meeting note-takers">None</td><td data-label="Work trackers">Internal boards</td><td data-label="Throughline" className="lp-us">Client view and drafted updates</td></tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section className="lp-section lp-alt" aria-labelledby="trust-h">
          <div className="lp-wrap">
            <p className="lp-eyebrow">Privacy</p>
            <h2 id="trust-h">Built to keep personal and work apart</h2>
            <div className="lp-grid3">
              <article className="lp-card"><h3>Your side stays yours</h3><p>An employer only sees work items you choose to share. Your personal conversations never cross over.</p></article>
              <article className="lp-card"><h3>Opt-in, read-only email</h3><p>Mail is read only when you connect it, and nothing is ever sent without your confirmation.</p></article>
              <article className="lp-card"><h3>Leave cleanly</h3><p>Leaving a company ends its access and wipes its data from your phone. You can delete your account anytime.</p></article>
            </div>
          </div>
        </section>

        <section id="pricing" className="lp-section" aria-labelledby="pricing-h">
          <div className="lp-wrap">
            <p className="lp-eyebrow">Pricing</p>
            <h2 id="pricing-h">Free while we’re in beta</h2>
            <p className="lp-sub">Start personal for free, or run a free pilot with your team. Per-seat pricing for Work comes after the pilot.</p>
            <div className="lp-plans">
              {PLANS.map((p) => (
                <article key={p.name} className={`lp-plan${p.featured ? ' lp-plan-featured' : ''}`}>
                  {p.featured && <p className="lp-plan-badge">For teams</p>}
                  <h3>{p.name}</h3>
                  <p className="lp-price">{p.price}</p>
                  <p className="lp-price-note">{p.note}</p>
                  <ul className="lp-plan-list">
                    {p.points.map((pt) => <li key={pt}><Check />{pt}</li>)}
                  </ul>
                  <a className={`lp-btn ${p.featured ? 'lp-btn-primary' : 'lp-btn-outline'} lp-btn-block`} href={p.href}>{p.cta}</a>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-final" aria-labelledby="final-h">
          <div className="lp-wrap lp-final-inner">
            <h2 id="final-h">Never lose track of what you said you’d do</h2>
            <div className="lp-hero-cta">
              {signedIn ? (
                <a className="lp-btn lp-btn-light lp-btn-lg" href={LOGIN}>Open Throughline</a>
              ) : (
                <>
                  <a className="lp-btn lp-btn-light lp-btn-lg" href={SIGNUP}>Create your account</a>
                  <a className="lp-btn lp-btn-onaccent lp-btn-lg" href={LOGIN}>Log in</a>
                </>
              )}
            </div>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-wrap lp-footer-row">
          <Logo />
          <nav aria-label="Legal" className="lp-footer-links">
            <a href="/privacy">Privacy</a>
            <a href="/terms">Terms</a>
            <a href={LOGIN}>Log in</a>
          </nav>
          <p className="lp-fine">© {new Date().getFullYear()} Throughline</p>
        </div>
      </footer>
    </div>
  );
}

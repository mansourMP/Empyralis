'use client';

import Link from 'next/link';

type LandingClientProps = {
  accountHref: string;
  accountLabel: string;
  primaryHref: string;
  primaryLabel: string;
  finalCtaLabel: string;
};

const platformPoints = [
  {
    title: 'Connect the apps you already use',
    body: 'Link accounts such as Google services and approved business tools so Sage can help with real work from one workspace.',
  },
  {
    title: 'Create native agents',
    body: 'Build template-based or custom Studio agents for focused workflows, then test them before they reach production channels.',
  },
  {
    title: 'Bring external agents safely',
    body: 'Register OpenClaw, Hermes, NemoClaw, MCP, A2A, or custom HTTPS agents through governed manifests and backend proxying.',
  },
  {
    title: 'Approve computer work',
    body: 'Agent Computer extends work to browsers, files, and selected machines only when the workspace policy allows it.',
  },
];

const useCases = [
  'Sage chat with connected apps',
  'Native business agents',
  'OpenClaw, Hermes, NemoClaw adapters',
  'Governed Agent Computer access',
];

const boundaryPoints = [
  {
    title: 'Native Studio agents',
    body: 'Built from templates or from scratch, tested privately, and promoted through explicit lifecycle controls.',
  },
  {
    title: 'Connected external agents',
    body: 'OpenClaw, Hermes, NemoClaw, MCP, A2A, and custom HTTPS agents connect through manifests, backend proxying, and secret references.',
  },
  {
    title: 'Agent Computer',
    body: 'Selected for local or private computer access. Studio agents and external agents do not inherit Sage Full Access.',
  },
];

export function LandingClient({
  accountHref,
  accountLabel,
  primaryHref,
  primaryLabel,
  finalCtaLabel,
}: LandingClientProps) {
  return (
    <main className="landing-root">
      <nav className="landing-nav" aria-label="Empyralis public navigation">
        <Link className="nav-logo" href="/" aria-label="Empyralis home">
          <img src="/brand-assets/empyralis/empyralis-hex-mark.svg" alt="" />
          <span>Empyralis</span>
        </Link>
        <div className="landing-nav__actions">
          <Link className="landing-button landing-button--quiet" href={accountHref}>
            {accountLabel}
          </Link>
          <Link className="landing-button landing-button--solid" href={primaryHref}>
            {primaryLabel}
          </Link>
        </div>
      </nav>

      <section className="landing-hero" aria-labelledby="landing-title">
        <div className="landing-hero__copy">
          <p className="landing-eyebrow">AI workspace for governed agents</p>
          <h1 id="landing-title">Chat with Sage. Bring your agents. Approve real actions.</h1>
          <p className="landing-hero__body">
            Empyralis brings Sage, native Studio agents, connected external agents, and approved computer access into one controlled workspace for real work.
          </p>
          <div className="landing-hero__actions">
            <Link className="landing-button landing-button--solid landing-button--large" href={primaryHref}>
              {finalCtaLabel}
            </Link>
            <Link className="landing-button landing-button--quiet landing-button--large" href="/privacy">
              Privacy policy
            </Link>
          </div>
        </div>

        <div className="landing-product-panel" aria-label="Empyralis summary">
          <div className="landing-product-panel__header">
            <img src="/brand-assets/empyralis/empyralis-hex-mark.svg" alt="" />
            <div>
              <strong>Empyralis</strong>
              <span>Connected workspace</span>
            </div>
          </div>
          <dl className="landing-product-list">
            <div>
              <dt>Primary assistant</dt>
              <dd>Sage</dd>
            </div>
            <div>
              <dt>Native agents</dt>
              <dd>Templates and custom agents</dd>
            </div>
            <div>
              <dt>Bring your agents</dt>
              <dd>OpenClaw, Hermes, NemoClaw, custom</dd>
            </div>
            <div>
              <dt>Controls</dt>
              <dd>Security policies and audit</dd>
            </div>
            <div>
              <dt>External boundary</dt>
              <dd>Manifests, proxying, secret refs</dd>
            </div>
            <div>
              <dt>Optional runtime</dt>
              <dd>Agent Computer</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="landing-section" aria-labelledby="platform-title">
        <div className="landing-section__header">
          <p className="landing-eyebrow">What it does</p>
          <h2 id="platform-title">One place for built-in and brought-in agents.</h2>
        </div>
        <div className="landing-card-grid">
          {platformPoints.map((point) => (
            <article className="landing-card" key={point.title}>
              <h3>{point.title}</h3>
              <p>{point.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section landing-section--boundary" aria-labelledby="boundary-title">
        <div className="landing-section__header">
          <p className="landing-eyebrow">Platform boundary</p>
          <h2 id="boundary-title">Built-in agents and external runtimes stay separate.</h2>
        </div>
        <div className="landing-boundary-list">
          {boundaryPoints.map((point) => (
            <article className="landing-boundary-item" key={point.title}>
              <h3>{point.title}</h3>
              <p>{point.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section landing-section--compact" aria-labelledby="use-cases-title">
        <div className="landing-section__header">
          <p className="landing-eyebrow">Common work</p>
          <h2 id="use-cases-title">Start with Sage, then add the agents and machines you trust.</h2>
        </div>
        <ul className="landing-use-list">
          {useCases.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <footer className="landing-footer">
        <span>Empyralis</span>
        <nav aria-label="Legal links">
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
          <Link href={accountHref}>{accountLabel}</Link>
        </nav>
      </footer>
    </main>
  );
}

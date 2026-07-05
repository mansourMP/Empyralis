'use client';

import Link from 'next/link';
import { type ReactNode, useEffect, useRef } from 'react';
import { Bot, Eye, DollarSign, Terminal, ShieldCheck } from 'lucide-react';

type LandingClientProps = {
  accountHref: string;      // /login
  accountLabel: string;     // Log in
  primaryHref: string;      // /signup
  primaryLabel: string;     // Get started
};

// ── Scroll reveal — CSS + IntersectionObserver, transform/opacity only.
// Content is visible by default (SSR / no-JS / reduced-motion safe); JS only
// *arms* the entrance (adds .lp-reveal), then reveals on scroll. This keeps the
// text in the first paint + accessibility tree instead of hiding it behind JS. ─

function Reveal({
  children,
  delay = 0,
  className,
  immediate = false,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
  immediate?: boolean; // above-the-fold: reveal on mount, never scroll-gated
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return; // stay visible
    el.classList.add('lp-reveal');
    if (delay) el.style.transitionDelay = `${delay}s`;
    // setTimeout (not rAF) so the reveal still fires in a hidden/background tab.
    if (immediate) {
      const id = window.setTimeout(() => el.classList.add('lp-reveal--in'), 30);
      return () => window.clearTimeout(id);
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            el.classList.add('lp-reveal--in');
            io.unobserve(el);
          }
        }
      },
      { threshold: 0.15, rootMargin: '0px 0px -8% 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [delay, immediate]);
  return (
    <div ref={ref} className={className}>
      {children}
    </div>
  );
}

// ── Abstracted agent-detail mock — real UI tokens, not a screenshot ───────────

function AgentMock() {
  return (
    <div className="lp-mock" role="img" aria-label="An Empyralis agent handling customer conversations on its own Telegram bot">
      <div className="lp-mock-head">
        <div className="lp-mock-avatar">S</div>
        <div className="lp-mock-id">
          <strong>Support Bot</strong>
          <span>Acme Corp · own agent</span>
        </div>
        <span className="lp-mock-status"><i className="lp-dot" />Online</span>
      </div>
      <div className="lp-mock-meta">
        <span className="lp-chip lp-chip--accent">Telegram · @acme_support_bot</span>
        <span className="lp-chip">Memory</span>
        <span className="lp-chip">GPT · Claude</span>
      </div>
      <div className="lp-mock-thread">
        <div className="lp-msg lp-msg--in"><span>Do you ship to Canada, and how long?</span></div>
        <div className="lp-msg lp-msg--out"><span>Yes — Canada ships in 4–6 business days. Want me to start an order?</span></div>
        <div className="lp-msg lp-msg--in"><span>Please, one blue, size L.</span></div>
      </div>
      <div className="lp-mock-foot">
        <span className="lp-mock-live"><i className="lp-dot lp-dot--pulse" />handling 3 conversations</span>
        <span className="lp-mock-cost">$0.42 today</span>
      </div>
    </div>
  );
}

// ── Small in-step visuals (real tokens) ───────────────────────────────────────

function StepCreate() {
  return (
    <div className="lp-step-visual" aria-hidden="true">
      <div className="lp-field"><span className="lp-field-label">Name</span><span className="lp-field-val">Support Bot</span></div>
      <div className="lp-field"><span className="lp-field-label">Job</span><span className="lp-field-val lp-field-val--pill">Customer support</span></div>
      <div className="lp-step-cta">Create agent</div>
    </div>
  );
}
function StepConnect() {
  return (
    <div className="lp-step-visual" aria-hidden="true">
      <div className="lp-connect-row"><span className="lp-connect-name">Telegram</span><span className="lp-chip lp-chip--accent">Connected</span></div>
      <div className="lp-connect-row lp-connect-row--muted"><span className="lp-connect-name">Discord</span><span className="lp-connect-add">+ Add</span></div>
      <div className="lp-connect-hint">@acme_support_bot</div>
    </div>
  );
}
function StepWork() {
  return (
    <div className="lp-step-visual" aria-hidden="true">
      <div className="lp-work-row"><i className="lp-dot" /><span>Resolved · refund policy</span></div>
      <div className="lp-work-row"><i className="lp-dot" /><span>Resolved · order status</span></div>
      <div className="lp-work-row lp-work-row--flag"><i className="lp-dot lp-dot--warn" /><span>Escalated · discount request</span></div>
    </div>
  );
}

const STEPS = [
  { n: '1', title: 'Create it', body: 'Tell it what to do. Pick a job — support, sales, ops — and it’s ready in minutes.', visual: <StepCreate /> },
  { n: '2', title: 'Give it a channel', body: 'It gets its own Telegram or Discord bot and talks to your customers directly. No shared inbox, no you in the middle.', visual: <StepConnect /> },
  { n: '3', title: 'It gets to work', body: 'It handles conversations end to end, remembers what matters, and flags the calls it shouldn’t make alone.', visual: <StepWork /> },
];

const PROOFS = [
  { icon: Bot, title: 'One agent, one job, its own everything', body: 'Every agent gets its own bot, its own logins, its own memory, and its own model. No shared state, no crossed wires.' },
  { icon: Eye, title: 'Watch the work', body: 'See every conversation your agent has with your customers, as it happens. Nothing runs in a black box.' },
  { icon: DollarSign, title: 'Honest costs', body: 'One dollar figure per agent and per project. Bring any AI provider — you always see exactly what you spend.' },
  { icon: Terminal, title: 'Your Claude or ChatGPT can drive it', body: 'Connect over MCP and your own assistant creates, configures, and manages your agents — just by asking.' },
  { icon: ShieldCheck, title: 'Safe by default', body: 'Sandboxed execution, isolated memory, and agents that escalate a decision instead of guessing.' },
];

export function LandingClient({ accountHref, accountLabel, primaryHref, primaryLabel }: LandingClientProps) {
  const scrollToHow = (e: React.MouseEvent) => {
    e.preventDefault();
    const el = document.getElementById('how');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div className="lp">
      <header className="lp-nav">
        <Link className="lp-brand" href="/" aria-label="Empyralis home">
          <img src="/brand-assets/empyralis/empyralis-hex-mark.svg" alt="" width={22} height={22} />
          <span>Empyralis</span>
        </Link>
        <nav className="lp-nav-actions" aria-label="Account">
          <Link className="lp-btn lp-btn--ghost" href={accountHref}>{accountLabel}</Link>
          <Link className="lp-btn lp-btn--solid" href={primaryHref}>{primaryLabel}</Link>
        </nav>
      </header>

      <main className="lp-main">
        {/* ── Hero ── */}
        <section className="lp-hero" aria-labelledby="lp-title">
          <Reveal className="lp-hero-copy" immediate>
            <p className="lp-eyebrow">Managed cloud agents</p>
            <h1 id="lp-title" className="lp-h1">Agents that do the work.</h1>
            <p className="lp-lede">
              Create a specialized agent in minutes. It gets its own Telegram bot, its own memory,
              and its own tools — then it handles your customers and tasks end to end, while you
              watch every conversation and every dollar.
            </p>
            <div className="lp-hero-cta">
              <Link className="lp-btn lp-btn--solid lp-btn--lg" href={primaryHref}>{primaryLabel}</Link>
              <a className="lp-btn lp-btn--ghost lp-btn--lg" href="#how" onClick={scrollToHow}>See how it works</a>
            </div>
          </Reveal>
          <Reveal className="lp-hero-visual" delay={0.08} immediate>
            <AgentMock />
          </Reveal>
        </section>

        {/* ── How it works ── */}
        <section id="how" className="lp-section" aria-labelledby="lp-how-title">
          <Reveal className="lp-section-head">
            <p className="lp-eyebrow">How it works</p>
            <h2 id="lp-how-title" className="lp-h2">From idea to a working agent in three steps.</h2>
          </Reveal>
          <div className="lp-steps">
            {STEPS.map((s, i) => (
              <Reveal className="lp-step" key={s.n} delay={i * 0.06}>
                <div className="lp-step-n">{s.n}</div>
                <h3 className="lp-step-title">{s.title}</h3>
                <p className="lp-step-body">{s.body}</p>
                {s.visual}
              </Reveal>
            ))}
          </div>
        </section>

        {/* ── Proof points ── */}
        <section className="lp-section" aria-labelledby="lp-proof-title">
          <Reveal className="lp-section-head">
            <p className="lp-eyebrow">Why it’s different</p>
            <h2 id="lp-proof-title" className="lp-h2">Real agents — not another chatbot.</h2>
          </Reveal>
          <div className="lp-proofs">
            {PROOFS.map((p, i) => {
              const Icon = p.icon;
              return (
                <Reveal className="lp-proof" key={p.title} delay={(i % 3) * 0.05}>
                  <span className="lp-proof-icon"><Icon size={18} strokeWidth={1.75} /></span>
                  <h3 className="lp-proof-title">{p.title}</h3>
                  <p className="lp-proof-body">{p.body}</p>
                </Reveal>
              );
            })}
          </div>
        </section>

        {/* ── MCP callout ── */}
        <section className="lp-section">
          <Reveal className="lp-mcp">
            <div className="lp-mcp-copy">
              <p className="lp-eyebrow">For Claude &amp; ChatGPT users</p>
              <h2 className="lp-h2">Run your fleet from the assistant you already use.</h2>
              <p className="lp-mcp-body">
                Connect Empyralis over MCP and your own assistant becomes the control panel — create an
                agent, give it a channel, read its work, check the cost. Just by asking.
              </p>
            </div>
            <div className="lp-mcp-code" aria-hidden="true">
              <div className="lp-code-line"><span className="lp-code-you">you</span> Create a support agent for Acme and give it a Telegram bot.</div>
              <div className="lp-code-line lp-code-line--reply"><span className="lp-code-a">assistant</span> Done. “Support Bot” is live on @acme_support_bot.</div>
            </div>
          </Reveal>
        </section>

        {/* ── Pricing placeholder ── */}
        <section className="lp-section" aria-labelledby="lp-price-title">
          <Reveal className="lp-price">
            <p className="lp-eyebrow">Pricing</p>
            <h2 id="lp-price-title" className="lp-h2">Usage-based. You see every dollar.</h2>
            <p className="lp-price-body">
              No seats, no tiers, no surprises. You pay for the work your agents do — and every agent
              and every project shows its own running cost, in plain dollars.
            </p>
            <Link className="lp-btn lp-btn--solid lp-btn--lg" href={primaryHref}>{primaryLabel}</Link>
          </Reveal>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-footer-brand">
          <img src="/brand-assets/empyralis/empyralis-hex-mark.svg" alt="" width={18} height={18} />
          <span>Empyralis</span>
        </div>
        <nav className="lp-footer-links" aria-label="Footer">
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
          <Link href={accountHref}>{accountLabel}</Link>
        </nav>
      </footer>
    </div>
  );
}

import Link from 'next/link';
import type { LucideIcon } from 'lucide-react';
import { Bot, Cpu, MessageSquare, SlidersHorizontal } from 'lucide-react';

import './landing.css';

const STEPS: Array<{ title: string; body: string }> = [
  {
    title: 'Pair your hardware',
    body: 'Your own computer, or a VPS one click away.',
  },
  {
    title: 'Give it a brain',
    body: 'Bring your own ChatGPT or Claude subscription, or use platform credits.',
  },
  {
    title: 'Connect it to the world',
    body: 'Real channels, or manage from Claude/ChatGPT via MCP.',
  },
];

const PILLARS: Array<{ icon: LucideIcon; copy: string }> = [
  { icon: Bot, copy: 'One agent, one job.' },
  { icon: Cpu, copy: 'Runs on hardware you own, with the subscription you already pay for.' },
  { icon: MessageSquare, copy: 'Reachable through real channels + MCP.' },
  { icon: SlidersHorizontal, copy: 'You decide exactly what each agent can do.' },
];

type LogoItem = { name: string; src: string };

const CHANNEL_LOGOS: LogoItem[] = [
  { name: 'Telegram', src: '/brand-assets/channels/telegram.svg' },
  { name: 'WhatsApp', src: '/brand-assets/channels/whatsapp.svg' },
  { name: 'Discord', src: '/brand-assets/channels/discord.svg' },
  { name: 'Slack', src: '/brand-assets/channels/slack.svg' },
  { name: 'WeChat', src: '/brand-assets/channels/wechat.svg' },
  { name: 'Signal', src: '/brand-assets/channels/signal.svg' },
  { name: 'iMessage', src: '/brand-assets/channels/imessage.svg' },
];

const PROVIDER_LOGOS: LogoItem[] = [
  { name: 'Anthropic', src: '/brand-assets/providers/anthropic.svg' },
  { name: 'OpenAI', src: '/brand-assets/providers/openai.svg' },
  { name: 'Gemini', src: '/brand-assets/providers/gemini.svg' },
  { name: 'DeepSeek', src: '/brand-assets/providers/deepseek.svg' },
  { name: 'xAI', src: '/brand-assets/providers/xai.svg' },
  { name: 'Mistral', src: '/brand-assets/providers/mistral.svg' },
];

const APP_LOGOS: LogoItem[] = [
  { name: 'GitHub', src: '/brand-assets/apps/github.svg' },
  { name: 'Notion', src: '/brand-assets/apps/notion.svg' },
  { name: 'Gmail', src: '/brand-assets/apps/gmail.svg' },
  { name: 'Stripe', src: '/brand-assets/apps/stripe.ico' },
  { name: 'Salesforce', src: '/brand-assets/apps/salesforce.ico' },
  { name: 'Linear', src: '/brand-assets/apps/linear.svg' },
];

const CAPABILITY_GROUPS: Array<{ title: string; body: string; logos: LogoItem[] }> = [
  {
    title: 'Channels',
    body: 'Reach your people where they already are.',
    logos: CHANNEL_LOGOS,
  },
  {
    title: 'Bring your own subscription',
    body: 'Use the AI subscription you already pay for — no separate API bill.',
    logos: PROVIDER_LOGOS,
  },
  {
    title: 'MCP + Applications',
    body: 'Connect the tools you already use, or manage everything from Claude & ChatGPT via MCP.',
    logos: APP_LOGOS,
  },
];

function LandingNav() {
  return (
    <header className="landing-nav">
      <div className="landing__container landing-nav__inner">
        <Link href="/" className="landing-nav__brand">
          <img
            src="/brand-assets/empyralis/empyralis-hex-mark.svg"
            alt=""
            width={24}
            height={24}
            className="landing-nav__mark"
          />
          Empyralis
        </Link>
        <div className="landing-nav__actions">
          <Link href="/login" className="landing-nav__login">
            Log in
          </Link>
          <Link href="/signup" className="app-button app-button--primary">
            Get started
          </Link>
        </div>
      </div>
    </header>
  );
}

function LandingHero() {
  return (
    <section className="landing__container landing-hero">
      <p className="landing-hero__kicker">Build your empire with Empyralis</p>
      <h1 className="landing-hero__title">
        Agents that handle the busywork, run your support, and work across your tools.
      </h1>
      <p className="landing-hero__subtitle">
        Empyralis agents automate the small stuff, answer your customers, and act across the
        tools you already use via MCP — running on hardware you control, powered by the AI
        subscription you already pay for.
      </p>
      <div className="landing-hero__cta">
        <Link href="/signup" className="app-button landing-cta--primary">
          Get started
        </Link>
      </div>
    </section>
  );
}

function WhyItExists() {
  return (
    <section className="landing-section landing-section--centered">
      <div className="landing__container landing-section__head">
        <h2 className="landing-section__heading">Why it exists</h2>
        <p className="landing-section__body">
          Cloud AI tools are brilliant but sandboxed — they can&rsquo;t run on your hardware, hold
          your accounts, or be reached by your customers. Real work needs an agent with a home and
          an identity.
        </p>
      </div>
    </section>
  );
}

function HowItWorks() {
  return (
    <section className="landing-section">
      <div className="landing__container">
        <div className="landing-section__head">
          <h2 className="landing-section__heading">How it works</h2>
        </div>
        <ol className="landing-steps">
          {STEPS.map((step, index) => (
            <li key={step.title} className="landing-step">
              <span className="landing-step__badge">{index + 1}</span>
              <h3 className="landing-step__title">{step.title}</h3>
              <p className="landing-step__body">{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function Pillars() {
  return (
    <section className="landing-section">
      <div className="landing__container">
        <div className="landing-section__head">
          <h2 className="landing-section__heading">What you get</h2>
        </div>
        <div className="landing-pillars">
          {PILLARS.map(({ icon: Icon, copy }) => (
            <div key={copy} className="landing-pillar">
              <span className="landing-pillar__icon">
                <Icon size={18} strokeWidth={2} aria-hidden="true" />
              </span>
              <p className="landing-pillar__copy">{copy}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Capabilities() {
  return (
    <section className="landing-section">
      <div className="landing__container">
        <div className="landing-section__head">
          <h2 className="landing-section__heading">What&rsquo;s already built in</h2>
        </div>
        <div className="landing-capability-groups">
          {CAPABILITY_GROUPS.map((group) => (
            <div key={group.title} className="landing-capability-group">
              <h3 className="landing-capability-group__title">{group.title}</h3>
              <p className="landing-capability-group__body">{group.body}</p>
              <div className="landing-logo-row">
                {group.logos.map((logo) => (
                  <span key={logo.name} className="landing-logo-row__item">
                    <img
                      src={logo.src}
                      alt={logo.name}
                      className="landing-logo-row__logo"
                      loading="lazy"
                    />
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function WhyNow() {
  return (
    <section className="landing-section landing-section--centered landing-why-now">
      <div className="landing__container landing-section__head">
        <h2 className="landing-section__heading">Why now</h2>
        <p className="landing-why-now__body">
          MCP standardized the interface, models got cheap and capable, hardware is one click
          away — the pieces for embodied agents just arrived.
        </p>
      </div>
    </section>
  );
}

function LandingFooter() {
  const year = new Date().getFullYear();
  return (
    <footer className="landing-footer">
      <div className="landing__container">
        <div className="landing-footer__cta">
          <h2 className="landing-footer__heading">Ready to get started?</h2>
          <Link href="/signup" className="app-button landing-cta--primary">
            Get started
          </Link>
        </div>
        <div className="landing-footer__bottom">
          <span>&copy; {year} Empyralis</span>
          <div className="landing-footer__links">
            <Link href="/privacy">Privacy</Link>
            <Link href="/terms">Terms</Link>
          </div>
        </div>
      </div>
    </footer>
  );
}

export function LandingPage() {
  return (
    <main className="landing">
      <LandingNav />
      <LandingHero />
      <WhyItExists />
      <HowItWorks />
      <Pillars />
      <Capabilities />
      <WhyNow />
      <LandingFooter />
    </main>
  );
}

export default function TermsPage() {
  return (
    <main className="app-static-page">
      <div className="app-static-page__content">
        <header className="app-static-page__header">
          <p className="app-static-page__kicker">Terms of Service</p>
          <h1 className="app-static-page__title">Empyralis Terms of Service</h1>
          <p className="app-static-page__body">
            Empyralis is a workspace where a team keeps its projects, tasks, and documents, and where AI agents do
            real work alongside that team — including, when you choose to set it up, running commands on a computer
            you own or provision. These Terms govern your access to and use of Empyralis (the &ldquo;Service&rdquo;).
            By creating an account or using the Service, you agree to these Terms on behalf of yourself or the
            organization you represent.
          </p>
          <p className="app-static-page__body">Last updated: August 27, 2026.</p>
          <p className="app-static-page__body">
            <strong>
              This document was drafted with AI assistance and has not yet been reviewed by a lawyer. It is meant to
              be an honest, accurate description of what Empyralis actually does today — not a legally sufficient
              contract. Do not rely on it as final until it has been reviewed by qualified counsel, and treat every
              bracketed note below as an open item.
            </strong>
          </p>
        </header>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">What Empyralis is</h2>
          <p className="app-static-page__body">
            Empyralis hosts projects that hold tasks and documents, and lets you create AI agents that work inside
            those projects. An agent can run in Empyralis&rsquo;s own cloud, or you can attach it to a computer —
            your own Mac, or a virtual server you provision through a cloud provider like DigitalOcean — that you or
            your organization own and control. People reach an agent through a messaging channel you connect, such
            as Telegram or Slack; Empyralis itself is not a chat product, and conversations with an agent happen in
            the channel you choose, not inside this website.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Agents run commands on hardware you control — read this carefully</h2>
          <p className="app-static-page__body">
            This is the most important thing to understand about Empyralis, so we are stating it plainly rather than
            burying it in a definitions section: when you attach an agent to a computer, that agent can execute
            shell commands and access files on that machine, on your instruction or on the instruction of anyone you
            allow to message it. Where the machine has Docker available, a command normally runs inside an isolated
            container; where it does not, the command runs directly on the computer itself. A separate,
            explicitly-enabled &ldquo;full access&rdquo; mode removes even that container boundary.
          </p>
          <p className="app-static-page__body">
            You are solely responsible for which computer you attach, what data and credentials live on it, and who
            you allow to reach the agent through a connected channel. Anyone who can message a hardware-attached
            agent can generally direct it to do anything that agent is capable of on that machine. Pairing hardware
            with an agent is an act of consent by the machine&rsquo;s owner, and you should not attach a computer you
            are not prepared to have an agent, and the people who can message it, act on.
          </p>
          <p className="app-static-page__body">
            Empyralis applies a narrow, non-negotiable set of protections on every hardware-attached command —
            blocking access to a fixed list of sensitive paths (such as SSH and credential directories) and a short
            list of destructive command patterns — but this is a floor, not a substitute for choosing carefully what
            you attach and who can reach it.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Accounts</h2>
          <p className="app-static-page__body">
            You need an account to use Empyralis. You agree to provide accurate information, to keep your login
            credentials confidential, and to be responsible for activity that happens under your account — including
            actions taken by agents you configure and by people you invite into your workspace. Tell us promptly if
            you believe your account has been accessed without authorization.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Connected accounts, credentials, and third-party providers</h2>
          <p className="app-static-page__body">
            Empyralis lets you connect third-party services — messaging channels, apps like Notion or Linear, and AI
            model providers — using OAuth connections or API keys/tokens you paste in. You are responsible for the
            connections you make, the permissions you grant, and the actions an agent takes through a connected
            service. See our Privacy Policy for how credentials and connection data are stored and, in some cases,
            passed through without being stored at all.
          </p>
          <p className="app-static-page__body">
            When an agent runs a turn, the text of your conversation and relevant workspace context is sent to the AI
            model provider configured for that agent — which may be a provider we operate on your behalf using
            platform credits, or a provider you connect with your own account or API key. That provider processes
            the content under its own terms and privacy practices, which we do not control. Message content sent
            through a connected channel (for example, Telegram or Slack) is also subject to that channel provider&rsquo;s
            own terms.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Acceptable use</h2>
          <p className="app-static-page__body">
            You may not use Empyralis to violate any law, abuse a third-party service or its terms, attempt to bypass
            access controls, send deceptive or unwanted messages through a connected channel, or direct an agent to
            act in a way that harms another person, another user&rsquo;s data, or any system — including a system
            reached through hardware you have attached to an agent.
          </p>
          <p className="app-static-page__body">
            You may upload notes and images to a project. You may not upload executable code, archives, or binary
            files as project attachments; the upload system checks the actual file content, not just its name, and
            will refuse a file that does not match what it claims to be.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Billing and credits</h2>
          <p className="app-static-page__body">
            Empyralis workspaces begin on a free plan. Usage of AI models we host for you is metered in credits,
            which you can purchase; the minimum purchase and available amounts are shown at checkout and may change.
            Payments are processed by Polar, acting as merchant of record — Polar, not Empyralis, is who your card
            statement will show. If you instead connect your own AI model API key or your own subscription to a
            provider (for example, an existing Claude, Codex, or Grok account), that usage is billed by that
            provider directly under your agreement with them, not by us.
          </p>
          <p className="app-static-page__body">
            [Empyralis&rsquo;s paid subscription tiers and their pricing are still being finalized and are not
            described here yet. This section will be updated once pricing is settled and reviewed.]
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">AI assistant behavior</h2>
          <p className="app-static-page__body">
            Agents on Empyralis act on their own reasoning; there is no approval step that blocks an agent
            mid-action. AI-generated output — including code, commands, messages sent through a channel, and content
            written to a task or document — may be incomplete or incorrect. You are responsible for reviewing
            important actions and outputs before relying on them, and for supervising what an agent you configure is
            permitted to do.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Your content</h2>
          <p className="app-static-page__body">
            You retain ownership of the projects, tasks, documents, and other content you or your agents create in
            Empyralis. You grant us the limited right to store, process, and transmit that content as needed to
            operate the Service — including sending it to the model providers and connected services you configure.
            We do not claim ownership of your content and do not sell it.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Service changes and availability</h2>
          <p className="app-static-page__body">
            Empyralis is an early-stage product and may change, add, remove, or limit features — including in ways
            that affect an agent&rsquo;s available tools or a channel&rsquo;s behavior — at any time, for reasons that include
            security, reliability, abuse prevention, and ordinary product development. We do not guarantee
            uninterrupted or error-free service, and we do not currently promise any specific uptime.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Disclaimers and limitation of liability</h2>
          <p className="app-static-page__body">
            The Service is provided &ldquo;as is&rdquo; and &ldquo;as available,&rdquo; without warranties of any
            kind, to the fullest extent the law where you are located allows. Because Empyralis can execute commands
            on hardware you attach, you understand and accept the real-world risk that entails — a command carried
            out at your instruction, or at the instruction of someone you allowed to reach the agent, is your
            responsibility, not ours.
          </p>
          <p className="app-static-page__body">
            [A specific liability cap, and the details of how disputes are resolved and under which country&rsquo;s law,
            are not stated here yet — they depend on how Empyralis is legally incorporated, which has not been
            finalized. A lawyer should complete this section once that is settled.]
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Termination</h2>
          <p className="app-static-page__body">
            You may stop using Empyralis and delete your account at any time. We may suspend or terminate your
            access if we believe you have violated these Terms, in particular the acceptable-use and hardware
            sections above, or if required to protect the Service or other users.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Changes to these Terms</h2>
          <p className="app-static-page__body">
            We may update these Terms as the product changes. If we make a material change, we will update the date
            above and, where practical, let you know. Continuing to use Empyralis after a change takes effect means
            you accept the updated Terms.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Contact</h2>
          <p className="app-static-page__body">
            Questions about these Terms can be sent to <a href="mailto:mansurao886@gmail.com">mansurao886@gmail.com</a>.
          </p>
          <p className="app-static-page__body">
            [The legal entity offering this Service, its registered address, and its jurisdiction of incorporation
            are not yet listed here and should be added once that entity is established.]
          </p>
        </section>
      </div>
    </main>
  );
}

export default function PrivacyPage() {
  return (
    <main className="app-static-page">
      <div className="app-static-page__content">
        <header className="app-static-page__header">
          <p className="app-static-page__kicker">Privacy Policy</p>
          <h1 className="app-static-page__title">Empyralis Privacy Policy</h1>
          <p className="app-static-page__body">
            Empyralis is a workspace for a team&rsquo;s projects, tasks, and documents, alongside AI agents that can
            act on that team&rsquo;s behalf — including, when configured, running commands on a computer the team owns
            or provisions. This policy explains what information we collect to operate Empyralis, why we collect it,
            and who else sees it.
          </p>
          <p className="app-static-page__body">Last updated: August 27, 2026.</p>
          <p className="app-static-page__body">
            <strong>
              This document was drafted with AI assistance and has not yet been reviewed by a lawyer. It is meant to
              be an honest, accurate description of what Empyralis actually does with your data today — not a
              legally sufficient privacy notice. Do not rely on it as final until it has been reviewed by qualified
              counsel, and treat every bracketed note below as an open item.
            </strong>
          </p>
        </header>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Who operates Empyralis</h2>
          <p className="app-static-page__body">
            Empyralis is operated by its founder, who is based in Uzbekistan.
          </p>
          <p className="app-static-page__body">
            [The specific legal entity that will operate Empyralis, its registered address, and its jurisdiction of
            incorporation have not been established yet and are not listed here. This section needs to be completed
            once that entity exists, and reviewed by a lawyer for what it implies about which country&rsquo;s privacy
            law applies to you.]
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Information you provide</h2>
          <p className="app-static-page__body">
            When you create an account, we collect your name, email address, and password (stored as a salted hash,
            never in plain text) — or, if you sign in with Google, the identity information Google provides. When
            you use the product, we store the projects, tasks, documents, and comments you and your teammates create,
            and any files you deliberately upload to a project (notes and images only — the upload system rejects
            executable code, archives, and binary files, checking the actual file content rather than trusting its
            name or extension).
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">What an agent generates and remembers</h2>
          <p className="app-static-page__body">
            When you configure an agent and it works on a task, we store the messages exchanged through the channel
            you connected, the agent&rsquo;s replies, records of the tools and commands it ran and their outcomes,
            and — where you or the agent choose to save one — a memory note about the project or a private note tied
            to your own account. These records live on our own servers; today that is a single server located in San
            Francisco, California, alongside a managed database that stores your workspace&rsquo;s structured data
            (accounts, tasks, documents, and similar records).
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Agents running commands on your computer</h2>
          <p className="app-static-page__body">
            If you attach an agent to a computer you own or provision, that agent can execute shell commands and
            read or write files on that machine, at your instruction or the instruction of someone you allow to
            message it. We do not store a general history of your computer&rsquo;s files; a summary of the specific
            commands run and their results is recorded as part of the agent&rsquo;s activity log described above, so
            that you can review what happened. Attaching hardware is a decision only the hardware&rsquo;s owner makes,
            and it is never inherited automatically by other members of a workspace.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Credentials and connected accounts</h2>
          <p className="app-static-page__body">
            When you connect a third-party account — an OAuth connection to an app like Google or Notion, an API key
            for an AI model provider, or a bot token for a messaging channel — we store what is needed to maintain
            that connection separately from ordinary workspace content, encrypted at rest, and use it only for the
            actions you configure or explicitly request.
          </p>
          <p className="app-static-page__body">
            One category of credential is different: for channels that run through a computer you have paired (for
            example, WhatsApp, Signal, or iMessage), the credential you paste in is sent directly to that computer
            and applied there. It passes through our servers but is not stored, logged, or retained by us at all —
            only its owner&rsquo;s own device holds it.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Third-party AI model providers</h2>
          <p className="app-static-page__body">
            When an agent responds to you, the content of your conversation and relevant workspace context is sent
            to an AI model provider to generate that response. Depending on how the agent is configured, this may be
            a provider we operate on your behalf (for example, DeepSeek), or a provider you have connected with your
            own account, subscription, or API key (which today may include Anthropic&rsquo;s Claude, OpenAI, xAI&rsquo;s
            Grok, Google&rsquo;s Gemini, and others, or a coding-assistant subscription such as Codex or Cursor).
            That provider processes the content under its own privacy practices, which we do not control and which
            you should review directly. We do not sell this data and do not use it to train our own models.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Messaging channels</h2>
          <p className="app-static-page__body">
            If you connect a messaging channel — such as Telegram or Slack, or, for agents attached to a paired
            computer, WhatsApp, Signal, iMessage, or a similar platform — messages sent to and from your agent pass
            through that channel&rsquo;s own service and are subject to that provider&rsquo;s own privacy practices in
            addition to this policy. We store the message content and delivery records needed to run the
            conversation and show it back to you in Empyralis.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Google user data</h2>
          <p className="app-static-page__body">
            Empyralis uses Google user data only after you grant consent through Google OAuth, and only to carry out
            the connected-app actions you authorize — for example, reading, drafting, or sending Gmail, or reading or
            creating Google Calendar events, when you ask an agent to do so. Empyralis does not sell Google user
            data, does not use it for advertising, and does not use it to train AI models.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Billing information</h2>
          <p className="app-static-page__body">
            Credit purchases are processed by Polar, acting as our merchant of record. We receive confirmation that a
            payment was made and the amount, but Polar — not Empyralis — collects and stores your payment card
            details.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Why we use this information</h2>
          <p className="app-static-page__body">
            We use the information above to operate the Service: authenticating you, running agents and delivering
            their replies, enforcing usage limits and storage caps, billing for credits, keeping the transparent
            record of tool calls and agent activity that Empyralis is built around, responding to support requests,
            and protecting the platform from abuse.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">International data transfer</h2>
          <p className="app-static-page__body">
            Empyralis is operated from Uzbekistan, our servers are located in the United States, and some
            third-party providers we use (including our payment processor and some AI model providers) are also
            U.S.-based. Using Empyralis means your information may be processed in these locations.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Retention and deletion</h2>
          <p className="app-static-page__body">
            You can delete individual tasks, documents, and connected accounts from within Empyralis. Disconnecting
            an account (for example, a Google connection) stops us from making new API requests through that
            connection. For a personal messaging channel, you can request deletion of your conversation history by
            sending <strong>/delete</strong> to the agent in that channel.
          </p>
          <p className="app-static-page__body">
            [Specific retention periods for each category of data — how long a deleted workspace&rsquo;s records are
            kept before permanent removal, how long billing and audit records are retained for legal reasons, and
            how a full account-deletion request is handled — are not finalized yet and should be defined before this
            policy is relied upon.]
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Children&rsquo;s privacy</h2>
          <p className="app-static-page__body">
            Empyralis is not directed to children and is not intended for use by anyone under 18.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Your rights</h2>
          <p className="app-static-page__body">
            Depending on where you live, applicable law may give you rights to access, correct, delete, or export
            your personal information, or to object to certain uses of it. You can exercise many of these directly
            in the product; for anything else, contact us using the details below.
          </p>
          <p className="app-static-page__body">
            [Whether GDPR, CCPA, or another specific privacy law applies to you, and the exact process and timeline
            for exercising rights under it, has not been determined and should be added once Empyralis&rsquo;s legal
            entity and customer base are established.]
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Changes to this policy</h2>
          <p className="app-static-page__body">
            We may update this policy as the product changes. If we make a material change, we will update the date
            above and, where practical, let you know.
          </p>
        </section>

        <section className="app-static-page__section">
          <h2 className="app-static-page__section-title">Contact</h2>
          <p className="app-static-page__body">
            Questions or privacy requests can be sent to <a href="mailto:mansurao886@gmail.com">mansurao886@gmail.com</a>.
          </p>
        </section>
      </div>
    </main>
  );
}

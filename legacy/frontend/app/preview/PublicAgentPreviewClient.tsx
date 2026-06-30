'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

type PublicMarketplaceAgent = {
  id: string;
  name: string;
  description: string;
  category?: string | null;
};

type PublicMarketplaceResponse = {
  items?: PublicMarketplaceAgent[];
};

export function PublicAgentPreviewClient({
  setupHref,
}: {
  setupHref: string;
}) {
  const [agents, setAgents] = useState<PublicMarketplaceAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function loadAgents() {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch('/api/marketplace/agents?limit=12', {
          method: 'GET',
          credentials: 'include',
          headers: {
            accept: 'application/json',
          },
        });
        if (!response.ok) {
          throw new Error(`Marketplace returned ${response.status}.`);
        }
        const payload = await response.json() as PublicMarketplaceResponse;
        if (!active) {
          return;
        }
        setAgents(Array.isArray(payload.items) ? payload.items : []);
      } catch (nextError) {
        if (!active) {
          return;
        }
        setError(nextError instanceof Error ? nextError.message : 'Could not load the public agent catalog.');
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void loadAgents();

    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="app-auth-page">
      <section className="app-message-card app-preview-card-shell">
        <div className="app-auth-header">
          <h1 className="app-auth-title">Explore agents</h1>
          <p className="app-auth-subtitle">Browse the public catalog before you set up your own.</p>
        </div>

        <div className="app-preview-list">
          {loading ? (
            <p className="app-auth-subtitle">Loading the public catalog…</p>
          ) : error ? (
            <p className="app-auth-error" role="alert">Public catalog could not load. Try again when ready.</p>
          ) : agents.length === 0 ? (
            <p className="app-auth-subtitle">No public agents are available right now.</p>
          ) : (
            agents.map((agent) => (
              <article key={agent.id} className="app-preview-card">
                <div className="app-preview-card__meta">
                  {agent.category ? <span className="app-preview-card__category">{agent.category}</span> : null}
                </div>
                <strong className="app-preview-card__title">{agent.name}</strong>
                <p className="app-preview-card__description">
                  {agent.description || 'Public agent preview'}
                </p>
              </article>
            ))
          )}
        </div>

        <div className="app-first-launch__actions">
          <Link href={setupHref} className="app-button app-button--primary app-first-launch__primary">
            Set up your personal agent
          </Link>
          <Link href="/" className="app-first-launch__secondary">
            Back
          </Link>
        </div>
      </section>
    </main>
  );
}

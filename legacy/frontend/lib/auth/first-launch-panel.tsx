import type { ReactNode } from 'react';

export function FirstLaunchPanel({
  primaryAction,
  secondaryAction,
}: {
  primaryAction: ReactNode;
  secondaryAction: ReactNode;
}) {
  return (
    <main className="app-auth-page">
      <section className="app-auth-card app-auth-card--launch">
        <div className="app-auth-header">
          <h1 className="app-auth-title">Empyralis</h1>
          <p className="app-auth-subtitle">Your workspace control surface for autonomous agents and private tools.</p>
        </div>
        <div className="app-inline-actions app-inline-actions--center">
          {primaryAction}
        </div>
        <div className="app-auth-footer">
          {secondaryAction}
        </div>
      </section>
    </main>
  );
}

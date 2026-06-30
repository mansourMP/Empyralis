import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="app-page-message">
      <div className="app-page-message__content">
        <h1 className="app-page-message__title">This route is not available</h1>
        <p className="app-page-message__body">
          Return to the authenticated login flow to continue inside an available workspace surface.
        </p>
        <div className="app-inline-actions">
          <Link href="/login" className="app-link-button app-link-button--primary">
            Open login
          </Link>
        </div>
      </div>
    </main>
  );
}

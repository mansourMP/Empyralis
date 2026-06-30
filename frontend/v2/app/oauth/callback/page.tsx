import { Suspense } from "react";
import OAuthCallbackContent from "./content";

export default function OAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-screen">
          <p className="text-zinc-400">Loading...</p>
        </div>
      }
    >
      <OAuthCallbackContent />
    </Suspense>
  );
}

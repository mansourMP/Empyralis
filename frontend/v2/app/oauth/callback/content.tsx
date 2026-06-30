"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

export default function OAuthCallbackContent() {
  const searchParams = useSearchParams();
  const [message, setMessage] = useState("Connecting...");

  useEffect(() => {
    const status = searchParams.get("status");
    const app = searchParams.get("app") || "app";

    if (status === "connected") {
      setMessage(`${app} connected. You can close this tab.`);
    } else {
      setMessage(`Could not connect ${app}. Try again from settings.`);
    }
  }, [searchParams]);

  return (
    <div className="flex items-center justify-center min-h-screen px-4">
      <div className="text-center space-y-4">
        <h1 className="text-xl font-semibold text-zinc-100">Empyralis</h1>
        <p className="text-zinc-400">{message}</p>
        <p className="text-zinc-600 text-sm">
          Return to the settings tab to continue.
        </p>
      </div>
    </div>
  );
}

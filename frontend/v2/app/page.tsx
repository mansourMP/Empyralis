"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const router = useRouter();
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    // Check for existing session first
    fetch(`${API}/session`, { credentials: "include" })
      .then((r) => r.json())
      .then((data) => {
        if (data.authenticated) {
          router.replace("/chat");
        } else {
          // No session — create a trial one automatically
          return fetch(`${API}/session`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          });
        }
      })
      .then((r) => r && r.json())
      .then((data) => {
        if (data && data.authenticated) {
          router.replace("/chat");
        } else {
          router.replace("/setup");
        }
      })
      .catch(() => router.replace("/setup"))
      .finally(() => setChecking(false));
  }, [router]);

  return (
    <div className="flex items-center justify-center min-h-screen">
      {checking && <p className="text-zinc-400">Loading...</p>}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface AppInfo {
  id: string;
  label: string;
  provider: string;
  connected: boolean;
}

export default function SettingsPage() {
  const router = useRouter();
  const [apps, setApps] = useState<AppInfo[]>([]);
  const [lastFour, setLastFour] = useState("");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    fetch(`${API}/session`, { credentials: "include" })
      .then((r) => r.json())
      .then((data) => {
        if (!data.authenticated) {
          router.replace("/setup");
          return;
        }
        setLastFour(data.last_four || "");
      })
      .catch(() => router.replace("/setup"));

    fetch(`${API}/apps`, { credentials: "include" })
      .then((r) => r.json())
      .then((data) => setApps(data.apps || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [router]);

  async function handleConnect(provider: string) {
    setMessage("");
    try {
      const res = await fetch(`${API}/oauth/start?app=${provider}`);
      if (!res.ok) {
        const err = await res.json();
        setMessage(`Error: ${err.detail}`);
        return;
      }
      const { url } = await res.json();
      window.open(url, "_blank");
    } catch (err: any) {
      setMessage(`Error: ${err.message}`);
    }
  }

  async function handleLogout() {
    await fetch(`${API}/session`, { method: "DELETE", credentials: "include" });
    router.push("/setup");
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <p className="text-zinc-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-8 space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-zinc-100">Settings</h1>
        <button
          onClick={() => router.push("/chat")}
          className="text-sm text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          Back to chat
        </button>
      </div>

      {/* API Key */}
      <section className="space-y-3">
        <h2 className="text-lg font-medium text-zinc-200">API Key</h2>
        <div className="flex items-center justify-between p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
          <span className="text-zinc-400 text-sm">
            Anthropic API key ending in {lastFour}
          </span>
          <button
            onClick={handleLogout}
            className="text-sm text-red-400 hover:text-red-300 transition-colors"
          >
            Sign out
          </button>
        </div>
      </section>

      {/* Connected Apps */}
      <section className="space-y-3">
        <h2 className="text-lg font-medium text-zinc-200">Connected Apps</h2>
        {message && (
          <p className="text-sm text-zinc-400 bg-zinc-900 border border-zinc-800 rounded-lg p-3">
            {message}
          </p>
        )}
        <div className="space-y-2">
          {/* Group by provider */}
          {[...new Set(apps.map((a) => a.provider))].map((provider) => {
            const providerApps = apps.filter((a) => a.provider === provider);
            const anyConnected = providerApps.some((a) => a.connected);
            return (
              <div
                key={provider}
                className="flex items-center justify-between p-4 bg-zinc-900 border border-zinc-800 rounded-lg"
              >
                <div>
                  <span className="text-zinc-200 text-sm font-medium">
                    {provider === "google_workspace" ? "Google Workspace" : provider}
                  </span>
                  <div className="text-zinc-500 text-xs mt-0.5">
                    {providerApps.map((a) => a.id).join(", ")}
                  </div>
                </div>
                {anyConnected ? (
                  <span className="text-sm text-green-400">Connected</span>
                ) : (
                  <button
                    onClick={() => handleConnect(provider)}
                    className="text-sm px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200
                               rounded-md transition-colors"
                  >
                    Connect
                  </button>
                )}
              </div>
            );
          })}
        </div>
        {apps.length === 0 && (
          <p className="text-zinc-500 text-sm">No apps configured.</p>
        )}
      </section>

      {/* Telegram Bot */}
      <section className="space-y-3">
        <h2 className="text-lg font-medium text-zinc-200">Telegram Bot</h2>
        <p className="text-zinc-500 text-sm">
          To run the Telegram bot, set TELEGRAM_BOT_TOKEN in your .env and run{" "}
          <code className="text-zinc-400 bg-zinc-900 px-1.5 py-0.5 rounded text-xs">
            python -m server.bot
          </code>
        </p>
      </section>
    </div>
  );
}

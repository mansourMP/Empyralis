"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function SetupPage() {
  const router = useRouter();
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const key = apiKey.trim();
    if (!key) return;

    setLoading(true);
    try {
      const res = await fetch(`${API}/session`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Invalid API key");
      }
      router.push("/chat");
    } catch (err: any) {
      setError(err.message || "Connection failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen px-4">
      <div className="w-full max-w-md space-y-6">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-100">Empyralis</h1>
          <p className="text-zinc-400 mt-1">
            Enter your API key to start using Sage.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-..."
            className="w-full px-4 py-3 bg-zinc-900 border border-zinc-700 rounded-lg
                       text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
            autoFocus
          />
          {error && (
            <p className="text-red-400 text-sm">{error}</p>
          )}
          <button
            type="submit"
            disabled={loading || !apiKey.trim()}
            className="w-full py-3 bg-zinc-100 text-zinc-900 rounded-lg font-medium
                       hover:bg-zinc-200 disabled:opacity-50 disabled:cursor-not-allowed
                       transition-colors"
          >
            {loading ? "Verifying..." : "Continue"}
          </button>
        </form>

        <p className="text-zinc-600 text-xs">
          Your key is stored encrypted in the vault. Only used for this session.
        </p>
      </div>
    </div>
  );
}

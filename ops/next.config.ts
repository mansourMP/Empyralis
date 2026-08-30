import type { NextConfig } from "next";

// Empyralis operator console — a completely separate Next.js app from
// frontend/. Unlike frontend/next.config.ts, `turbopack.root` here is
// pinned to THIS directory rather than the repo root: this app imports
// nothing outside its own directory (see lib/theme-tokens.css's and
// lib/view-state.ts's own header comments for why a couple of small modules
// are a deliberate COPY of a frontend/lib/ file rather than a cross-app
// import), so it has no reason to widen its root the way frontend/ does.
// Explicit rather than left to Turbopack's own inference: this repo has TWO
// lockfiles (the root package-lock.json for the Tauri/mobile tooling, and
// this app's own) once `npm install` runs here, and Turbopack's root
// auto-detection picks whichever directory it finds a lockfile in first —
// observed picking the REPO ROOT during a real `next build`, which is
// exactly the accidental cross-app reach this app's whole existence is
// supposed to rule out. Pinning it removes the ambiguity instead of hoping
// the inference keeps guessing right.
const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  turbopack: { root: __dirname },
};

export default nextConfig;

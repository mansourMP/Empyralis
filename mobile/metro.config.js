/**
 * THE WHOLE POINT OF THIS FILE: `frontend/lib/**` is imported by this app
 * UNCHANGED — not copied, not extracted into a package, not re-implemented.
 *
 * The 40-odd pure modules under frontend/lib/workspace/fleet/ encode the
 * product's real rules (which channel doors exist, how a card face is
 * decided, how the Inbox ranks). The Swift app had to hand-port them and
 * then write tests whose only job was checking the Swift copy still matched
 * the web copy — a whole class of drift that stops existing the moment ONE
 * file serves both apps.
 *
 * Two settings do it, and neither copies a byte:
 *
 *   watchFolders      Metro only watches the project root by default, so a
 *                     file outside `mobile/` is invisible to it — not an
 *                     import error, a "module not found". Adding the repo's
 *                     frontend/lib puts those files in the graph.
 *
 *   extraNodeModules  maps the bare specifier `@shared/...` onto that
 *                     directory. Relative imports INSIDE the shared tree
 *                     (`./fleet-presentation`) then resolve on their own,
 *                     which is why no per-module wiring is needed.
 *
 * nodeModulesPaths is pinned to this app's own node_modules because a file
 * outside the project root otherwise resolves packages by walking UP from
 * its own directory — frontend/node_modules (absent in an agent worktree),
 * then the repo root. Pinning it means a shared module that imports `react`
 * gets React Native's copy rather than whatever the web tree happens to
 * have installed.
 */
const path = require('path');
const { getDefaultConfig } = require('expo/metro-config');

const projectRoot = __dirname;
const repoRoot = path.resolve(projectRoot, '..');
const sharedRoot = path.resolve(repoRoot, 'frontend/lib');

const config = getDefaultConfig(projectRoot);

config.watchFolders = [sharedRoot];

config.resolver.nodeModulesPaths = [path.resolve(projectRoot, 'node_modules')];
config.resolver.extraNodeModules = {
  ...(config.resolver.extraNodeModules || {}),
  '@shared': sharedRoot,
};

module.exports = config;

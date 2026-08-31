// A real `server-only` install is either absent (plain `require` throws
// MODULE_NOT_FOUND) or, if ever added as a dependency, throws unconditionally
// outside a bundler's "react-server" export condition (see
// control-plane-proxy.test.ts's own comment for the full story). Test files
// that need to import a 'server-only'-guarded module under plain `npx tsx`
// redirect that one bare specifier here via a `Module._resolveFilename`
// patch — this file is the target: a real, committed, do-nothing module.
module.exports = {};

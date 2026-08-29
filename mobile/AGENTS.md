# mobile/ — the cross-platform app

Expo (React Native), TypeScript, iOS + Android from one source. It replaces
the native Swift app in `ios-app/`, which is still running and is still the
reference until this is better — **do not touch or delete `ios-app/`.**

Expo has changed a lot; read the exact versioned docs for the SDK in
`package.json` (currently 57) at https://docs.expo.dev/versions/v57.0.0/
rather than working from memory. The repo's own rule applies here too: ask
the running harness first, documentation last.

## The point of this app: `frontend/lib/**` is imported, not ported

The ~40 pure modules under `frontend/lib/workspace/fleet/` encode the
product's real rules. The Swift app had to hand-port them and then write
tests whose only job was checking the port still matched. Here they are the
same file:

```ts
import { planInboxNeedsYou } from '@shared/workspace/fleet/inbox-needs-you';
```

Two settings make that work and neither copies a byte — see `metro.config.js`
for the full reasoning:

| where | what |
| --- | --- |
| `metro.config.js` | `watchFolders` puts `frontend/lib` in the bundler graph; `extraNodeModules` maps `@shared` onto it |
| `tsconfig.json` | the same alias for the type-checker, plus `@/*` because the shared tree uses the web's own alias internally |

**Metro reads `tsconfig.json`'s `paths`.** An alias added only to satisfy
`tsc` also rewires the bundler — aliasing `react` to `@types/react` type-checks
perfectly and produces a bundle that cannot find React. Anything needed only
by the type-checker goes in `tsconfig.typecheck.json`, which is what
`npm run typecheck` uses.

## Running it

```sh
npm run typecheck          # tsc over this app AND the shared tree
npm test                   # the shared-module drift guard
npx expo export --platform all   # bundles both platforms; proves resolution
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 npx expo run:ios
npx expo run:android       # needs the Android SDK + NDK, see below
```

**`ios/` and `android/` are generated and gitignored** (Continuous Native
Generation). Deleting either is safe; `expo run:*` regenerates it.

### Four environment traps, each of which cost real time

**CocoaPods needs a UTF-8 locale or it dies mid-`pod install`** with
`Unicode Normalization not appropriate for ASCII-8BIT` out of
`Pod::Config#installation_root`. It is not a Podfile problem and the message
never says "locale". Export `LANG`/`LC_ALL` as above.

**Android needs a JDK, and the Temurin CASK cannot be installed
non-interactively** — it shells out to `sudo` and blocks on a password. The
Homebrew FORMULA installs into the prefix with no sudo:
`brew install openjdk@17`, then `JAVA_HOME=/opt/homebrew/opt/openjdk@17`.

**Gradle refuses to configure without NDK 27.1.12297006** (~2.4 GB), even
though nothing here compiles C++ — React Native's root plugin pins
`ndkVersion` and auto-installs it. Plus a system image for the emulator.
Prefer `system-images;android-36;default;arm64-v8a` over the `google_apis`
variant: this app needs no Play services and the default image is a fraction
of the size, which matters on a full disk. Budget several GB, and check `df`
FIRST — a `sdkmanager` run that runs out of space reports
`Warning: An error occurred while preparing SDK package`, keeps going, and
leaves an empty directory tree that looks installed.

**Expo 57 renamed the Android icon assets.** Its template emits
`android-icon-foreground/-background/-monochrome.png`; an `adaptiveIcon`
pointing at the old `adaptive-icon.png` fails prebuild.

A dev stack on ports 8513/3513 is assumed in `src/api/config.ts`.
**Android cannot reach `127.0.0.1`** — the emulator has its own network
namespace, so it uses `10.0.2.2`, and the failure without it is a bare
"Network request failed" that reads exactly like a dead backend.

## Sign-in

There is no second auth path here. The app opens the real website in a
system browser sheet and gets a single-use code back on `empyralis://auth`
(PKCE); `server_modules/native_auth_service.py` and
`frontend/lib/auth/native-login-handoff.ts` are the two halves that already
existed. What happens INSIDE that sheet is system-owned and cannot be
automated or read — so email/password behind a quiet link is the only path a
test can drive, which is why it exists.

The backend's redirect table has exactly one key, `"ios"`, whose value is the
bare custom scheme this one app registers on both platforms — so Android
works through it today. The key is misnamed; the mechanism is not.

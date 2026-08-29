import { Platform } from 'react-native';

/**
 * Where the app talks to.
 *
 * ANDROID CANNOT REACH 127.0.0.1 AND WILL NOT SAY SO USEFULLY. The emulator
 * runs in its own network namespace, so loopback is the EMULATOR's loopback,
 * not the host's — every request fails as a bare "Network request failed",
 * which reads exactly like a dead backend. 10.0.2.2 is the emulator's alias
 * for the host. iOS Simulator shares the host's network stack, so it does
 * not need the swap.
 *
 * The production origin is a single https host with no per-platform branch,
 * because the shape above is a development-only accident of emulators.
 */
const DEV_HOST = Platform.OS === 'android' ? '10.0.2.2' : '127.0.0.1';

const DEV_BACKEND_PORT = 8513;
const DEV_WEB_PORT = 3513;

/** The REST API root. Every path in this app is appended to it. */
export const API_BASE_URL = __DEV__
  ? `http://${DEV_HOST}:${DEV_BACKEND_PORT}`
  : 'https://empyralis.ai';

/**
 * The website the sign-in sheet opens. Sign-in is the website's job, not the
 * app's — see session.ts. That is also why there is no Google client id
 * anywhere in this project.
 */
export const WEB_ORIGIN = __DEV__
  ? `http://${DEV_HOST}:${DEV_WEB_PORT}`
  : 'https://empyralis.ai';

/**
 * The custom scheme the sign-in sheet redirects back into. It matches
 * app.json's `scheme`, and — the reason a custom scheme was chosen over a
 * universal link — it needs no Associated Domains entitlement on iOS and no
 * verified App Link on Android, so it works on a free personal Apple team
 * and on any Android build.
 *
 * The backend names this URL itself (native_auth_service.NATIVE_REDIRECT_
 * TARGETS) and the app only ever picks a KEY. This constant exists to
 * REGISTER the listener, never to build the redirect.
 */
export const NATIVE_REDIRECT_URI = 'empyralis://auth';

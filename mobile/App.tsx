import { useCallback, useEffect, useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import { StyleSheet, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

import { forgetInbox } from './src/api/inbox';
import { clearSession, loadStoredSession, type Session } from './src/api/session';
import { TabBar, type TabKey } from './src/components/TabBar';
import { InboxScreen } from './src/screens/InboxScreen';
import { SignInScreen } from './src/screens/SignInScreen';
import { StubScreen } from './src/screens/StubScreen';
import { Theme } from './src/theme';

/**
 * PHASE 1 USES NO NAVIGATION LIBRARY, and that is a decision rather than a
 * shortcut. There are four destinations, three of them stubs, and the tab
 * bar is a bespoke floating pill that a library's own tab bar would have to
 * be replaced with anyway. A surface must earn its place; so must a
 * dependency. Every screen stays MOUNTED behind the switch rather than
 * being unmounted per tab — that is what makes returning to the Inbox
 * instant, and it is also the trap the iOS app hit from the other side
 * (a routed tab remounts and silently loses local state).
 */
export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [restoring, setRestoring] = useState(true);
  const [tab, setTab] = useState<TabKey>('inbox');
  const [inboxCount, setInboxCount] = useState(0);
  const [askAi, setAskAi] = useState(false);

  useEffect(() => {
    void (async () => {
      setSession(await loadStoredSession());
      setRestoring(false);
    })();
  }, []);

  const signOut = useCallback(() => {
    // Drop the in-memory inbox as well as the keychain entry. It is the only
    // copy of this person's inbox the process holds, and the next sign-in
    // paints from cache on its first frame.
    forgetInbox();
    void clearSession();
    setSession(null);
    setInboxCount(0);
    setAskAi(false);
  }, []);

  // A blank page while the keychain is read — deliberately nothing, not a
  // spinner and not the sign-in screen. Flashing "Get started" at someone
  // who is already signed in is a lie the restore is about to correct.
  if (restoring) {
    return (
      <SafeAreaProvider>
        <View style={styles.root} />
        <StatusBar style="light" />
      </SafeAreaProvider>
    );
  }

  if (!session) {
    return (
      <SafeAreaProvider>
        <View style={styles.root}>
          <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
            <SignInScreen onSignedIn={setSession} />
          </SafeAreaView>
        </View>
        <StatusBar style="light" />
      </SafeAreaProvider>
    );
  }

  return (
    <SafeAreaProvider>
      <View style={styles.root}>
        <SafeAreaView style={styles.safe} edges={['top']}>
          <View style={[styles.pane, (askAi || tab !== 'inbox') && styles.hidden]}>
            <InboxScreen session={session} onSignOut={signOut} onCountChange={setInboxCount} />
          </View>
          {askAi ? <StubScreen title="Ask AI" /> : null}
          {!askAi && tab === 'projects' ? <StubScreen title="Projects" /> : null}
          {!askAi && tab === 'mywork' ? <StubScreen title="My work" /> : null}
          {!askAi && tab === 'agents' ? <StubScreen title="Agents" /> : null}
        </SafeAreaView>
        <TabBar
          active={tab}
          onSelect={(next) => {
            setAskAi(false);
            setTab(next);
          }}
          inboxBadge={inboxCount}
          askAiActive={askAi}
          // Pressing it says it is not built. Doing NOTHING was the first
          // version and it is worse than a stub: a button that swallows a
          // press is indistinguishable from a broken one. What Ask AI
          // becomes on a phone is an open question (it is the one surface on
          // the web that still has a composer, and whether it keeps one is
          // undecided) — so this shows its POSITION without guessing at its
          // contents.
          onAskAi={() => setAskAi(true)}
        />
      </View>
      <StatusBar style="light" />
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: Theme.bgPage },
  safe: { flex: 1, backgroundColor: Theme.bgPage },
  pane: { flex: 1 },
  // `display: none` rather than unmounting — see the header comment. An
  // explicit display beats a `hidden`-style prop here, the same trap noted
  // on the web side.
  hidden: { display: 'none' },
});

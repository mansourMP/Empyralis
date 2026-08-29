import { useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import {
  signInThroughWebsite,
  signInWithPassword,
  type Session,
  type SignInOutcome,
} from '../api/session';
import { BrandMark } from '../components/BrandMark';
import { Radius, Space, Theme, Type } from '../theme';

/**
 * The welcome screen is a logo and one button — the founder's own read of
 * Linear's: "There is a logo, there is not even a brand name. There is just
 * a brand logo and it says start", and pressing it opens what looks like
 * Safari.
 *
 * "Get started" IS the primary action, so it carries the view's one accent.
 * The email door sits below it as a quiet link, exactly as the iOS app has
 * it — it exists because the browser sheet is system-owned and cannot be
 * automated, so this is the only path a test can drive.
 */
export function SignInScreen({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const [mode, setMode] = useState<'welcome' | 'email'>('welcome');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canSubmit = email.trim().length > 0 && password.length > 0;

  async function run(work: () => Promise<SignInOutcome>) {
    setBusy(true);
    setError(null);
    const outcome = await work();
    setBusy(false);
    if (outcome.kind === 'signed-in') {
      onSignedIn(outcome.session);
      return;
    }
    // A dismissed sheet is not a failure and must not be painted as one.
    if (outcome.kind === 'cancelled') return;
    setError(outcome.message);
  }

  return (
    <KeyboardAvoidingView
      style={styles.screen}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.markWrap}>
          <BrandMark size={84} />
        </View>

        {mode === 'welcome' ? (
          <View style={styles.block}>
            <Pressable
              accessibilityRole="button"
              disabled={busy}
              onPress={() => void run(signInThroughWebsite)}
              style={({ pressed }) => [styles.primary, pressed && styles.primaryPressed]}
            >
              {busy ? (
                <ActivityIndicator color={Theme.accentText} />
              ) : (
                <Text style={styles.primaryLabel}>Get started</Text>
              )}
            </Pressable>
            <Pressable accessibilityRole="button" onPress={() => setMode('email')} hitSlop={8}>
              <Text style={styles.quietLink}>Sign in with email instead</Text>
            </Pressable>
          </View>
        ) : (
          <View style={styles.block}>
            <TextInput
              accessibilityLabel="Email"
              placeholder="Email"
              placeholderTextColor={Theme.textMuted}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              value={email}
              onChangeText={setEmail}
              style={styles.field}
            />
            <TextInput
              accessibilityLabel="Password"
              placeholder="Password"
              placeholderTextColor={Theme.textMuted}
              secureTextEntry
              value={password}
              onChangeText={setPassword}
              style={styles.field}
            />
            {/* Disabled until both fields have something in them. An empty
                submit round-trips to the server and comes back "Request
                validation failed." — mechanism-flavoured copy in front of a
                customer, for a request that could never have succeeded.
                Observed live, not theorised. */}
            <Pressable
              accessibilityRole="button"
              disabled={busy || !canSubmit}
              onPress={() => void run(() => signInWithPassword(email, password))}
              style={({ pressed }) => [
                styles.primary,
                !canSubmit && styles.primaryDisabled,
                pressed && canSubmit && styles.primaryPressed,
              ]}
            >
              {busy ? (
                <ActivityIndicator color={Theme.accentText} />
              ) : (
                <Text style={[styles.primaryLabel, !canSubmit && styles.primaryLabelDisabled]}>Continue</Text>
              )}
            </Pressable>
            <Pressable accessibilityRole="button" onPress={() => setMode('welcome')} hitSlop={8}>
              <Text style={styles.quietLink}>Back</Text>
            </Pressable>
          </View>
        )}

        {error ? <Text style={styles.error}>{error}</Text> : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Theme.bgPage },
  content: { flexGrow: 1, justifyContent: 'center', paddingHorizontal: Space.x6, gap: Space.x8 },
  markWrap: { alignItems: 'center' },
  block: { gap: Space.x3, alignItems: 'stretch' },
  // The view's ONE accent-filled button.
  primary: {
    height: 52,
    borderRadius: Radius.control,
    backgroundColor: Theme.accent,
    alignItems: 'center',
    justifyContent: 'center',
  },
  primaryPressed: { opacity: 0.85 },
  primaryDisabled: { backgroundColor: Theme.bgLift },
  primaryLabel: { ...Type.button, color: Theme.accentText },
  primaryLabelDisabled: { color: Theme.textMuted },
  quietLink: {
    ...Type.rowSubtitle,
    color: Theme.textMuted,
    textAlign: 'center',
    paddingVertical: Space.x2,
  },
  field: {
    height: 52,
    borderRadius: Radius.control,
    paddingHorizontal: Space.x4,
    backgroundColor: Theme.bgCard,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: Theme.border,
    color: Theme.textPrimary,
    ...Type.body,
  },
  error: { ...Type.rowSubtitle, color: Theme.danger, textAlign: 'center' },
});

import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Space, Theme, Type } from '../theme';

/**
 * THE LIST ROW. Every number below was MEASURED off a 3x screenshot of the
 * app this is held to, not chosen — the previous version was designed from
 * a description and the founder's verdict on it was "totally fucking
 * stupid", so the method changed as much as the result.
 *
 *   [ 40pt icon well ]  13pt  • title
 *                             reason · age
 *
 *   row pitch 69pt   ·   icon well 40pt, fill #2E2E2E, glyph #989898
 *   text column starts 73pt in   ·   NO DIVIDERS, spacing separates rows
 *
 * THERE ARE NO SECTION HEADERS ON THIS SCREEN, and the row is what makes
 * that possible. `NEEDS YOUR INPUT 2 / NOTIFICATIONS 4 / FAILED RUNS 2`
 * shouted three times about groups a person never asked to see; the
 * reference has none, because the row already says what it is TWICE — the
 * glyph in its well, and the reason it leads its subtitle with. The
 * grouping still exists where it belongs, in the RANKING (see InboxScreen).
 *
 * THE DOT IS INLINE, NOT IN A GUTTER — measured: a read row's title starts
 * at x=218 and an unread one's at x=262, i.e. the dot occupies real
 * horizontal space and the title shifts when it goes. A reserved gutter
 * leaves a hole down the left of a list that is mostly read.
 *
 * THE ICON WELL DOES NOT DIM. Measured identical on read and unread rows
 * (#2E2E2E fill, #989898 glyph, both) — which is not what "the whole row
 * dims" would predict, and is right: the wells are the list's left rail,
 * and a rail that fades in patches reads as a rendering fault. Only the
 * text carries read state, in two levels so the title/subtitle hierarchy
 * survives inside the dimmed state.
 */

/** The three things that can land here. The kind picks the glyph; it never
 *  picks the wording — `inbox-needs-you.ts` already wrote that. */
export type InboxRowKind = 'task' | 'notification' | 'run';

const GLYPH: Record<InboxRowKind, keyof typeof Ionicons.glyphMap> = {
  // A task of yours that is stuck and waiting on you.
  task: 'checkbox-outline',
  // Something addressed to you: a mention, an assignment, a comment.
  notification: 'notifications-outline',
  // A run that stopped and did not finish.
  run: 'warning-outline',
};

export function InboxRow({
  kind,
  title,
  reason,
  age,
  unread,
  onPress,
}: {
  kind: InboxRowKind;
  title: string;
  reason: string | null;
  age: string;
  unread: boolean;
  onPress?: () => void;
}) {
  const dim = !unread;

  const body = (
    <View style={styles.row}>
      <View style={styles.well}>
        <Ionicons name={GLYPH[kind]} size={19} color={Theme.textMuted} />
      </View>
      <View style={styles.text}>
        <View style={styles.titleLine}>
          {unread ? <View style={styles.dot} /> : null}
          <Text style={[styles.title, dim && styles.titleDim]} numberOfLines={1}>
            {title}
          </Text>
        </View>
        {/* TWO TEXTS, NOT ONE INTERPOLATED STRING — and this was a real bug
            caught by looking at the rendered screen rather than the code.
            `${reason} · ${age}` on one truncating line lets a long reason
            eat the age: six of eight rows rendered
            `New comment on "Port the Inbox ranking" ·…` with no age at all.
            The age is the only temporal anchor a row has, and "17d ago" is
            the exact fact this whole surface exists to surface. So the
            reason shrinks and the age never does. */}
        <View style={styles.subtitleLine}>
          {reason ? (
            <Text style={[styles.subtitle, dim && styles.subtitleDim]} numberOfLines={1}>
              {reason}
            </Text>
          ) : null}
          <Text style={[styles.subtitle, styles.age, dim && styles.subtitleDim]}>
            {reason ? ` · ${age}` : age}
          </Text>
        </View>
      </View>
    </View>
  );

  // A row with nowhere to go is not pressable. Phase 1 has no task or agent
  // detail screen, so most rows render as plain text — the shared module
  // says so itself by handing back a null href, and wrapping them in a
  // Pressable that does nothing would be exactly the dead control that
  // contract exists to avoid.
  if (!onPress) return body;

  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      style={({ pressed }) => pressed && styles.pressed}
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  // 40 + 14 + 14 = 68, against a measured pitch of 69.
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Space.x5,
    paddingVertical: 14,
  },
  pressed: { backgroundColor: Theme.bgInset },
  well: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: Theme.bgRaised,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 13,
  },
  text: { flex: 1, gap: 3 },
  titleLine: { flexDirection: 'row', alignItems: 'center' },
  // 10pt, and it sits ON the title's own line rather than above or beside
  // the whole block, so a two-line row could never orphan it.
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: Theme.unread,
    marginRight: 8,
  },
  // flexShrink so a long title ellipsises instead of pushing the dot out.
  title: { ...Type.rowTitle, color: Theme.textPrimary, flexShrink: 1 },
  titleDim: { color: Theme.textDimmed },
  subtitleLine: { flexDirection: 'row', alignItems: 'baseline' },
  // flexShrink on the reason, and NOT on the age, is the whole mechanism:
  // the row runs out of width in the reason, never in the age.
  subtitle: { ...Type.rowSubtitle, color: Theme.textMuted, flexShrink: 1 },
  age: { flexShrink: 0 },
  subtitleDim: { color: Theme.textDimmedDeep },
});

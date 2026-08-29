import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Radius, Space, Theme } from '../theme';

/**
 * THE TAB BAR: a floating dark pill lifted off the bottom edge, four items,
 * the selected one marked by a lighter PILL BEHIND IT. Then a SEPARATE,
 * DETACHED round button to its right.
 *
 *      [ ▣   ▤•   ◎   ◈ ]   ( ✦ )
 *
 * ICONS ONLY — NO LABELS, and that was the founder's own note against the
 * reference. Four words under four icons is a caption strip explaining
 * glyphs that are already the standard ones; the reference carries none,
 * and dropping them is what lets the bar be 56pt instead of 58 with two
 * lines crammed in. The label survives as `accessibilityLabel`, which is
 * where a name a sighted person does not need still has to exist.
 *
 * MEASURED: pill 272 x 56pt, 28pt side margins, 28pt off the bottom;
 * selected pill ~70pt wide, fill #2E2E2E against the bar's own #1C1C1C.
 * That 20-level delta is the whole selection signal and it is why
 * `bgRaised` had to exist — `bgLift` is 8 levels off bgCard and simply
 * does not read on a black page.
 *
 * SELECTION IS WEIGHT AND SHAPE, NEVER HUE. The lighter pill, plus a FILLED
 * glyph where the others are outlines. This is the same rule the web
 * settled after five passes — `--accent` belongs to the single primary
 * action in a view, and a tab is navigation, not a primary action. There is
 * deliberately no violet anywhere in this file.
 *
 * ASK AI IS DETACHED ON PURPOSE, and its position is the argument. The
 * reference puts its agent exactly there: always reachable, never a place
 * you ARE. Putting it inside the pill would make it a fifth destination and
 * imply the app has a chat surface — which this product explicitly does not
 * ("messaging would never be done inside this platform").
 */
export type TabKey = 'projects' | 'inbox' | 'mywork' | 'agents';

/** Outline when idle, solid when selected — the shape half of the
 *  selection signal, so it never rests on fill colour alone. */
const TABS: {
  key: TabKey;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  iconSelected: keyof typeof Ionicons.glyphMap;
}[] = [
  { key: 'projects', label: 'Projects', icon: 'albums-outline', iconSelected: 'albums' },
  { key: 'inbox', label: 'Inbox', icon: 'file-tray-outline', iconSelected: 'file-tray' },
  {
    key: 'mywork',
    label: 'My work',
    icon: 'checkmark-circle-outline',
    iconSelected: 'checkmark-circle',
  },
  { key: 'agents', label: 'Agents', icon: 'sparkles-outline', iconSelected: 'sparkles' },
];

export function TabBar({
  active,
  onSelect,
  inboxBadge,
  askAiActive,
  onAskAi,
}: {
  active: TabKey;
  onSelect: (key: TabKey) => void;
  inboxBadge: number;
  /** While Ask AI is open no TAB is where you are, so none of them may read
   *  as selected — two things claiming to be the current place is the same
   *  bug as two pickers on one screen. */
  askAiActive: boolean;
  onAskAi: () => void;
}) {
  const insets = useSafeAreaInsets();

  return (
    // Measured 28pt off the physical bottom — less than the 34pt home-
    // indicator inset, which is why this is not simply `insets.bottom`.
    // Floored at 12 so a device with no inset at all still lifts the bar.
    <View style={[styles.wrap, { paddingBottom: Math.max(insets.bottom - 6, Space.x3) }]}>
      <View style={styles.pill}>
        {TABS.map((tab) => {
          const selected = !askAiActive && tab.key === active;
          // The badge is a COUNT, not a decoration, so it is only rendered
          // when there is something to count. A "0" would be a control
          // whose own label admits it means nothing.
          const showBadge = tab.key === 'inbox' && inboxBadge > 0;
          return (
            <Pressable
              key={tab.key}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              accessibilityLabel={tab.label}
              onPress={() => onSelect(tab.key)}
              style={[styles.tab, selected && styles.tabSelected]}
            >
              <View>
                <Ionicons
                  name={selected ? tab.iconSelected : tab.icon}
                  size={20}
                  color={selected ? Theme.textPrimary : Theme.textSecondary}
                />
                {showBadge ? (
                  // The ring matches the surface the badge actually sits
                  // on. Hard-coding bgCard leaves a visible dark halo the
                  // moment the Inbox tab is the selected one — which, on
                  // the tab that carries the badge, is most of the time.
                  <View
                    style={[
                      styles.badge,
                      { borderColor: selected ? Theme.bgRaised : Theme.bgCard },
                    ]}
                  />
                ) : null}
              </View>
            </Pressable>
          );
        })}
      </View>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Ask AI"
        accessibilityState={{ selected: askAiActive }}
        onPress={onAskAi}
        style={({ pressed }) => [
          styles.askAi,
          askAiActive && styles.askAiActive,
          pressed && styles.askAiPressed,
        ]}
      >
        <Ionicons
          name={askAiActive ? 'chatbubble-ellipses' : 'chatbubble-ellipses-outline'}
          size={20}
          color={Theme.textPrimary}
        />
      </Pressable>
    </View>
  );
}

const BAR_HEIGHT = 56;

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 9,
    paddingHorizontal: 28,
  },
  pill: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    height: BAR_HEIGHT,
    paddingHorizontal: Space.x1,
    borderRadius: Radius.pill,
    backgroundColor: Theme.bgCard,
  },
  tab: {
    flex: 1,
    alignSelf: 'stretch',
    alignItems: 'center',
    justifyContent: 'center',
    marginVertical: Space.x1 + 1,
    borderRadius: Radius.pill,
  },
  tabSelected: { backgroundColor: Theme.bgRaised },
  badge: {
    position: 'absolute',
    top: -2,
    right: -4,
    width: 9,
    height: 9,
    borderRadius: 4.5,
    backgroundColor: Theme.unread,
    // Ringed in the surface it sits on, so it stays a distinct dot rather
    // than merging into the glyph underneath it. The colour is supplied at
    // the call site because that surface changes with selection.
    borderWidth: 2,
  },
  askAi: {
    width: BAR_HEIGHT,
    height: BAR_HEIGHT,
    borderRadius: Radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Theme.bgCard,
  },
  askAiPressed: { backgroundColor: Theme.bgRaised },
  // Same weight-and-shape treatment the selected tab pill uses. No hue.
  askAiActive: { backgroundColor: Theme.bgRaised },
});

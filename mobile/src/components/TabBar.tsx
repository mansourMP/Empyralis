import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Radius, Space, Theme, Type } from '../theme';

/**
 * THE TAB BAR, per the design language read off Linear's real app:
 *
 *   a FLOATING dark pill lifted off the bottom edge, four items, the
 *   selected one marked by a lighter PILL BEHIND IT — never by colour.
 *   Then a SEPARATE, DETACHED round button to its right.
 *
 *      [ Projects   Inbox•   My work   Agents ]   ( Ask AI )
 *
 * ASK AI IS DETACHED ON PURPOSE, and its position is the argument. Linear
 * puts their agent exactly there: always reachable, never a place you ARE.
 * Putting it inside the pill would make it a fifth destination and imply
 * the app has a chat surface — which this product explicitly does not
 * ("messaging would never be done inside this platform"). Outside the pill
 * it reads as an action, which is what it is.
 *
 * SELECTION IS WEIGHT AND SHAPE, NEVER HUE. The lighter pill plus a
 * brightened label carries it. This is the same rule the web settled after
 * five passes — `--accent` belongs to the single primary action in a view,
 * and a tab is navigation, not a primary action. There is deliberately no
 * violet anywhere in this file.
 */
export type TabKey = 'projects' | 'inbox' | 'mywork' | 'agents';

const TABS: { key: TabKey; label: string; icon: keyof typeof Ionicons.glyphMap }[] = [
  { key: 'projects', label: 'Projects', icon: 'albums-outline' },
  { key: 'inbox', label: 'Inbox', icon: 'file-tray-outline' },
  { key: 'mywork', label: 'My work', icon: 'checkmark-circle-outline' },
  { key: 'agents', label: 'Agents', icon: 'sparkles-outline' },
];

export function TabBar({
  active,
  onSelect,
  inboxBadge,
  onAskAi,
}: {
  active: TabKey;
  onSelect: (key: TabKey) => void;
  inboxBadge: number;
  onAskAi: () => void;
}) {
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.wrap, { paddingBottom: Math.max(insets.bottom, Space.x3) }]}>
      <View style={styles.pill}>
        {TABS.map((tab) => {
          const selected = tab.key === active;
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
                  name={tab.icon}
                  size={20}
                  color={selected ? Theme.textPrimary : Theme.textMuted}
                />
                {showBadge ? <View style={styles.badge} /> : null}
              </View>
              <Text style={[styles.label, selected && styles.labelSelected]} numberOfLines={1}>
                {tab.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Ask AI"
        onPress={onAskAi}
        style={({ pressed }) => [styles.askAi, pressed && styles.askAiPressed]}
      >
        <Ionicons name="chatbubble-ellipses-outline" size={20} color={Theme.textPrimary} />
      </Pressable>
    </View>
  );
}

const BAR_HEIGHT = 58;

const styles = StyleSheet.create({
  wrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Space.x2,
    paddingHorizontal: Space.x4,
  },
  pill: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    height: BAR_HEIGHT,
    paddingHorizontal: Space.x1 + 2,
    borderRadius: Radius.pill,
    backgroundColor: Theme.bgCard,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: Theme.border,
  },
  tab: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 2,
    paddingVertical: Space.x1 + 2,
    marginVertical: Space.x1 + 1,
    borderRadius: Radius.pill,
  },
  tabSelected: { backgroundColor: Theme.bgLift },
  label: { ...Type.tab, color: Theme.textMuted },
  labelSelected: { color: Theme.textPrimary },
  badge: {
    position: 'absolute',
    top: -1,
    right: -3,
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: Theme.unread,
    borderWidth: 1.5,
    borderColor: Theme.bgCard,
  },
  askAi: {
    width: BAR_HEIGHT,
    height: BAR_HEIGHT,
    borderRadius: Radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Theme.bgCard,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: Theme.border,
  },
  askAiPressed: { backgroundColor: Theme.bgLift },
});

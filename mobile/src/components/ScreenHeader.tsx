import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Space, Theme, Type } from '../theme';

/**
 * THE HEADER IDIOM: a large bold left title, and on the right the screen's
 * actions sharing ONE rounded container — never loose floating buttons.
 *
 * MEASURED: the container is 88 x 44pt, filled #1C1C1C, sitting 20pt off
 * the right edge, and there is NO DIVIDER BETWEEN ITS ICONS. That last one
 * is not a detail — a hairline scan straight across the reference's own
 * container at mid-height comes back flat #1B1B1B end to end. The divider
 * this used to draw made two buttons read as a segmented control, i.e. a
 * choice between two modes, which is not what they are.
 *
 * THE CONTAINER IS THE IDIOM, NOT A FIXED COUNT OF TWO. So this takes a
 * list and renders however many are real. A screen with one honest action
 * gets a container of one; padding it out to two would mean rendering a
 * control that does nothing, which this product treats as a design bug
 * rather than a caption.
 */
export type HeaderAction = {
  key: string;
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
};

export function ScreenHeader({ title, actions }: { title: string; actions: HeaderAction[] }) {
  return (
    <View style={styles.row}>
      <Text style={styles.title} numberOfLines={1}>
        {title}
      </Text>
      {actions.length > 0 ? (
        <View style={styles.actions}>
          {actions.map((action) => (
            <Pressable
              key={action.key}
              accessibilityRole="button"
              accessibilityLabel={action.label}
              onPress={action.onPress}
              style={({ pressed }) => [styles.action, pressed && styles.actionPressed]}
            >
              <Ionicons name={action.icon} size={19} color={Theme.textSecondary} />
            </Pressable>
          ))}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: Space.x5,
    paddingTop: Space.x1,
    paddingBottom: Space.x3,
  },
  title: {
    ...Type.title,
    color: Theme.textPrimary,
    flexShrink: 1,
  },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    height: 44,
    borderRadius: 22,
    backgroundColor: Theme.bgCard,
    overflow: 'hidden',
  },
  // 44pt of touch target each way — the container's own height, and wide
  // enough that two of them make the measured 88pt.
  action: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionPressed: { backgroundColor: Theme.bgRaised },
});

import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Radius, Space, Theme, Type } from '../theme';

/**
 * THE HEADER IDIOM: a large bold left title, and on the right the screen's
 * actions sharing ONE rounded container — never loose floating buttons.
 *
 * THE CONTAINER IS THE IDIOM, NOT A FIXED COUNT OF TWO. Linear's own header
 * carries `+ ···` on a list and `✎ ···` on a detail, i.e. whatever that
 * screen genuinely does. So this takes a list and renders however many are
 * real, hairline-divided. A screen with one honest action gets a container
 * of one; padding it out to two would mean rendering a control that does
 * nothing, which this product treats as a design bug rather than a caption.
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
          {actions.map((action, index) => (
            <View key={action.key} style={styles.actionSlot}>
              {index > 0 ? <View style={styles.divider} /> : null}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={action.label}
                onPress={action.onPress}
                style={({ pressed }) => [styles.action, pressed && styles.actionPressed]}
                hitSlop={6}
              >
                <Ionicons name={action.icon} size={18} color={Theme.textSecondary} />
              </Pressable>
            </View>
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
    paddingTop: Space.x2,
    paddingBottom: Space.x4,
  },
  title: {
    ...Type.title,
    color: Theme.textPrimary,
    flexShrink: 1,
  },
  actions: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: Theme.bgCard,
    borderRadius: Radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: Theme.border,
    overflow: 'hidden',
  },
  actionSlot: { flexDirection: 'row', alignItems: 'center' },
  divider: { width: StyleSheet.hairlineWidth, height: 20, backgroundColor: Theme.border },
  action: { paddingHorizontal: Space.x3, paddingVertical: Space.x2 + 2 },
  actionPressed: { backgroundColor: Theme.bgLift },
});

import { Ionicons } from '@expo/vector-icons';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';

import { Radius, Space, Theme, Type } from '../theme';

/**
 * WHAT "···" OPENS.
 *
 * The header used to carry a LOGOUT ARROW as a top-level action, beside the
 * screen's own verb. The founder read that off the reference and said so:
 * the reference's header is one thing this screen DOES plus an overflow,
 * and signing out is neither the Inbox's verb nor something a person should
 * be one mis-tap from. It moves here.
 *
 * A MODAL, NOT AN IN-SCREEN OVERLAY, and the reason is the tab bar: it is a
 * sibling of the screen, so an overlay inside the screen leaves it live and
 * pressable behind a menu that is supposed to have taken the foreground.
 * `onRequestClose` is what gives Android's back gesture the same job as the
 * backdrop, rather than backing out of the app with a menu open.
 *
 * Anchored under the header's own container rather than centred, because a
 * menu that appears somewhere other than the control that opened it makes a
 * person hunt for the connection.
 */
export type OverflowItem = {
  key: string;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  /** Rendered in the danger hue and placed last by the caller. Reserved for
   *  something a person would not want to hit by accident. */
  destructive?: boolean;
  onPress: () => void;
};

export function OverflowMenu({
  visible,
  onClose,
  items,
  /** Distance from the top of the WINDOW — status bar included — to where
   *  the menu should hang. The caller computes it, deliberately: a Modal is
   *  its own view hierarchy and `useSafeAreaInsets()` read from inside one
   *  can come back all zeros, which would park this menu under the status
   *  bar and on top of the header that opened it. Outside the Modal the
   *  inset is simply correct. */
  anchorTop,
}: {
  visible: boolean;
  onClose: () => void;
  items: OverflowItem[];
  anchorTop: number;
}) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
      statusBarTranslucent
    >
      <Pressable style={styles.backdrop} onPress={onClose} accessibilityLabel="Close menu" />
      <View style={[styles.card, { top: anchorTop }]} pointerEvents="box-none">
        <View style={styles.menu}>
          {items.map((item) => (
            <Pressable
              key={item.key}
              accessibilityRole="button"
              onPress={() => {
                // Close first: every item here either navigates or changes
                // what the screen underneath is showing, and a menu still
                // sitting over the result is a second thing to dismiss.
                onClose();
                item.onPress();
              }}
              style={({ pressed }) => [styles.item, pressed && styles.itemPressed]}
            >
              <Text style={[styles.label, item.destructive && styles.labelDestructive]}>
                {item.label}
              </Text>
              <Ionicons
                name={item.icon}
                size={18}
                color={item.destructive ? Theme.danger : Theme.textSecondary}
              />
            </Pressable>
          ))}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  // Written out rather than spread from StyleSheet.absoluteFillObject, which
  // this React Native version does not type.
  backdrop: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: 'rgba(0,0,0,0.5)',
  },
  card: { position: 'absolute', right: Space.x5, left: Space.x5, alignItems: 'flex-end' },
  menu: {
    minWidth: 200,
    borderRadius: Radius.card,
    backgroundColor: Theme.bgRaised,
    paddingVertical: Space.x1,
    overflow: 'hidden',
  },
  item: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Space.x4,
    paddingHorizontal: Space.x4,
    paddingVertical: Space.x3,
  },
  itemPressed: { backgroundColor: Theme.bgLift },
  label: { ...Type.body, color: Theme.textPrimary },
  labelDestructive: { color: Theme.danger },
});

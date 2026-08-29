import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FlatList, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { ApiError } from '../api/client';
import {
  cachedInbox,
  fetchInbox,
  inboxNeedsYouCount,
  markNotificationRead,
  setInboxUser,
  timeAgo,
  type InboxData,
} from '../api/inbox';
import type { Session } from '../api/session';
import { InboxRow } from '../components/InboxRow';
import { OverflowMenu, type OverflowItem } from '../components/OverflowMenu';
import { ScreenHeader, type HeaderAction } from '../components/ScreenHeader';
import { SkeletonList, SurfaceEmpty, SurfaceFailure } from '../components/SurfaceStates';
import { Space, Theme, Type } from '../theme';

/**
 * INBOX — "what needs you, across every agent, right now."
 *
 * ONE FLAT LIST. NO SECTION HEADERS. The previous version shouted
 * `NEEDS YOUR INPUT 2` / `NOTIFICATIONS 4` / `FAILED RUNS 2` down the
 * screen; the app we are held to has none, and the founder's verdict on
 * ours was unambiguous. Headers here were answering a question nobody
 * asked — a person opening this wants to know what needs them, not how the
 * three sources it was assembled from are apportioned. Each row already
 * says which kind it is twice over (its glyph, and the reason it leads its
 * subtitle with), which is exactly how the reference does it: the reason is
 * the subtitle, not a header.
 *
 * THE GROUPING SURVIVES WHERE IT MATTERS — IN THE ORDER. `planInboxNeedsYou`
 * ranks the three groups on three different, deliberately incompatible
 * clocks: stuck tasks OLDEST-first (the founder's own 17-day task is the
 * reason that rule exists), notifications and runs newest-first. Flattening
 * is a CONCATENATION in group order and never a merge — re-sorting the
 * union by timestamp would need one "urgency" score across unrelated units
 * and would re-bury the exact row this surface was built to surface. Every
 * item keeps the index the grouped rendering gave it; only the headings
 * between them are gone.
 *
 * Nothing here decides order, membership or wording. That is all
 * `inbox-needs-you.ts`, imported unchanged (see src/api/inbox.ts).
 */
export function InboxScreen({
  session,
  onSignOut,
  onCountChange,
}: {
  session: Session;
  onSignOut: () => void;
  onCountChange: (count: number) => void;
}) {
  // Local-first: paint the last good answer on the first frame. `hasLoaded`
  // is tracked separately from `refreshing` on purpose — only "nothing is
  // known yet" may draw a skeleton, because a spinner over content already
  // on screen lies about what is known.
  const [data, setData] = useState<InboxData | null>(() => cachedInbox(session.userId));
  const [failure, setFailure] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [readIds, setReadIds] = useState<Set<string>>(new Set());
  const [menuOpen, setMenuOpen] = useState(false);
  const mounted = useRef(true);
  const insets = useSafeAreaInsets();

  useEffect(() => {
    setInboxUser(session.userId);
  }, [session.userId]);

  // Its OWN effect, with no dependencies, and it re-arms on the way in.
  // Folded into the effect above, the cleanup would fire whenever the user
  // id changed rather than only on unmount, latching `mounted` false for the
  // rest of the screen's life — and under StrictMode's deliberate
  // mount/unmount/mount it would latch false before the first load ever ran,
  // so the screen would sit on its skeleton forever with no error anywhere.
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const next = await fetchInbox(session.workspaceId, session.token);
      if (!mounted.current) return;
      setData(next);
      setFailure(null);
    } catch (error) {
      if (!mounted.current) return;
      if (error instanceof ApiError && error.failure.kind === 'unauthorized') {
        onSignOut();
        return;
      }
      // A failed refresh never blanks content that is already true. The
      // list keeps rendering; the failure is reported beside it.
      setFailure(error instanceof Error ? error.message : 'Could not load your inbox.');
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  }, [session.workspaceId, session.token, onSignOut]);

  useEffect(() => {
    void load();
  }, [load]);

  // THE FLATTEN. Concatenation in group order — tasks, then notifications,
  // then runs — which is the order the grouped rendering drew them in, so
  // dropping the headings changed no row's position. There is deliberately
  // no local re-ordering anywhere in this app; ordering belongs to the
  // shared module, and a second opinion about it is exactly the drift this
  // app exists to make impossible. Guarded in shared-module-drift.test.mjs.
  const items = useMemo(() => {
    if (!data) return [];
    return [...data.groups.tasks, ...data.groups.notifications, ...data.groups.runs];
  }, [data]);

  // TWO NUMBERS, because they answer different questions and one number
  // would have to lie about one of them.
  //
  //   count      every row on this screen. Decides the empty state — with
  //              read notifications still in the list (unread_only=false),
  //              "all caught up" over visible rows would be a flat lie.
  //   attention  the rows currently showing a dot. Decides the tab badge,
  //              so the badge means EXACTLY what the dots mean and goes
  //              quiet as they are dealt with. Using `count` here would
  //              leave the badge lit forever the moment anything was read.
  const count = data ? inboxNeedsYouCount(data.groups) : 0;

  // Read state lives beside the plan rather than inside it — the shared
  // module's items are documented as carrying stable `kind:id` keys, which
  // is the intended seam. Optimistic: the row dims immediately and the
  // server call follows.
  const unreadById = useMemo(() => {
    const map = new Map<string, boolean>();
    (data?.notifications ?? []).forEach((n) => {
      map.set(`notification:${n.id}`, !n.is_read && !readIds.has(String(n.id)));
    });
    return map;
  }, [data?.notifications, readIds]);

  const onReadNotification = useCallback(
    (itemId: string) => {
      const rawId = itemId.replace(/^notification:/, '');
      setReadIds((prev) => new Set(prev).add(rawId));
      void markNotificationRead(session.workspaceId, rawId, session.token).catch(() => {
        // Roll back rather than leave the row claiming a state the server
        // does not hold.
        setReadIds((prev) => {
          const next = new Set(prev);
          next.delete(rawId);
          return next;
        });
      });
    },
    [session.workspaceId, session.token],
  );

  const hasUnread = Array.from(unreadById.values()).some(Boolean);

  const attention =
    (data?.groups.tasks.length ?? 0) +
    (data?.groups.runs.length ?? 0) +
    Array.from(unreadById.values()).filter(Boolean).length;

  useEffect(() => {
    onCountChange(attention);
  }, [attention, onCountChange]);

  const markAllRead = useCallback(() => {
    (data?.notifications ?? []).forEach((n) => {
      if (!n.is_read) onReadNotification(`notification:${n.id}`);
    });
  }, [data?.notifications, onReadNotification]);

  // ONE VERB PLUS AN OVERFLOW — the reference's own header shape, and the
  // fix to a real complaint: a SIGN-OUT ARROW used to sit here as a
  // top-level action, which is neither this screen's verb nor something to
  // be one mis-tap from. It is behind "···" now, with Refresh.
  //
  // Mark all read is only rendered when there is something to mark. A
  // "mark all read" over an already-read list is a control whose own label
  // admits it does nothing — so the container is sometimes one wide, which
  // is the honest shape rather than a padded pair.
  const actions: HeaderAction[] = [];
  if (hasUnread) {
    actions.push({
      key: 'read-all',
      icon: 'checkmark-done-outline',
      label: 'Mark all read',
      onPress: markAllRead,
    });
  }
  actions.push({
    key: 'more',
    icon: 'ellipsis-horizontal',
    label: 'More',
    onPress: () => setMenuOpen(true),
  });

  const menuItems: OverflowItem[] = [
    { key: 'refresh', label: 'Refresh', icon: 'refresh-outline', onPress: () => void load() },
    {
      key: 'signout',
      label: 'Sign out',
      icon: 'log-out-outline',
      destructive: true,
      onPress: onSignOut,
    },
  ];

  const header = (
    <>
      <ScreenHeader title="Inbox" actions={actions} />
      <OverflowMenu
        visible={menuOpen}
        onClose={() => setMenuOpen(false)}
        items={menuItems}
        // The header's own height: 4 top + a 44 action container + 12
        // bottom, then 6 of air — measured from below the status bar, so
        // the inset is added here rather than inside the Modal, where it
        // can read as zero.
        anchorTop={insets.top + 66}
      />
    </>
  );

  const nothingKnownYet = !data;

  if (nothingKnownYet && failure) {
    return (
      <View style={styles.screen}>
        {header}
        <SurfaceFailure
          title="Couldn’t load your inbox"
          message={failure}
          onRetry={() => void load()}
        />
      </View>
    );
  }

  if (nothingKnownYet) {
    return (
      <View style={styles.screen}>
        {header}
        <SkeletonList />
      </View>
    );
  }

  if (count === 0) {
    return (
      <View style={styles.screen}>
        {header}
        <SurfaceEmpty title="You’re all caught up" body="Nothing is waiting on you right now." />
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      {header}
      <FlatList
        data={items}
        keyExtractor={(item) => item.id}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => void load()}
            tintColor={Theme.textMuted}
          />
        }
        ListHeaderComponent={
          failure ? (
            <Text style={styles.staleNotice}>
              Showing what was last loaded — couldn’t refresh just now.
            </Text>
          ) : null
        }
        renderItem={({ item }) => (
          <InboxRow
            // Deliberately NOT cast. InboxRow keeps its own copy of the
            // kind union because the drift test allows exactly one importer
            // of the shared module — so if `InboxNeedsYouKind` ever grows a
            // fourth member, this line must fail to compile rather than
            // render a row with no glyph. A cast here would make that
            // silent, which is the whole failure mode this app exists to
            // rule out.
            kind={item.kind}
            title={item.title}
            reason={item.detail}
            age={timeAgo(item.timestamp)}
            // Tasks and runs are unattended by definition — they are on this
            // screen precisely because they are still stuck or still failed.
            // Only a notification has a read state of its own.
            unread={unreadById.has(item.id) ? Boolean(unreadById.get(item.id)) : true}
            onPress={item.kind === 'notification' ? () => onReadNotification(item.id) : undefined}
          />
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Theme.bgPage },
  // Clears the floating tab bar (56 + 28 lift) plus air, so the last row is
  // never parked underneath it.
  list: { paddingBottom: 110 },
  staleNotice: {
    ...Type.rowSubtitle,
    color: Theme.warning,
    paddingHorizontal: Space.x5,
    paddingBottom: Space.x3,
  },
});

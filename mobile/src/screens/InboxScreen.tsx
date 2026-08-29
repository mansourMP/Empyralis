import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';

import { ApiError } from '../api/client';
import {
  cachedInbox,
  dateParsingReport,
  fetchInbox,
  inboxNeedsYouCount,
  markNotificationRead,
  setInboxUser,
  timeAgo,
  type InboxData,
} from '../api/inbox';
import type { Session } from '../api/session';
import { InboxRow } from '../components/InboxRow';
import { ScreenHeader, type HeaderAction } from '../components/ScreenHeader';
import { SkeletonList, SurfaceEmpty, SurfaceFailure } from '../components/SurfaceStates';
import { Space, Theme, Type } from '../theme';

/**
 * INBOX — "what needs you, across every agent, right now."
 *
 * Every ranking, grouping and reason string on this screen comes out of
 * `inbox-needs-you.ts`, the web's own module, imported unchanged (see
 * src/api/inbox.ts). This file renders; it decides nothing about order or
 * membership. In particular the three groups keep their own internally
 * consistent orders — stuck tasks OLDEST-first, notifications and runs
 * newest-first — which is the module's deliberate design and the reason a
 * task that has sat for seventeen days does not get buried under whatever
 * moved most recently.
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
  const mounted = useRef(true);

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

  const actions: HeaderAction[] = [];
  // Only rendered when there is something to mark. A "mark all read" over an
  // already-read list is a control whose own label admits it does nothing.
  if (hasUnread) {
    actions.push({
      key: 'read-all',
      icon: 'checkmark-done-outline',
      label: 'Mark all read',
      onPress: () => {
        (data?.notifications ?? []).forEach((n) => {
          if (!n.is_read) onReadNotification(`notification:${n.id}`);
        });
      },
    });
  }
  actions.push({ key: 'signout', icon: 'log-out-outline', label: 'Sign out', onPress: onSignOut });

  const nothingKnownYet = !data;

  if (nothingKnownYet && failure) {
    return (
      <View style={styles.screen}>
        <ScreenHeader title="Inbox" actions={actions} />
        <SurfaceFailure title="Couldn’t load your inbox" message={failure} onRetry={() => void load()} />
      </View>
    );
  }

  if (nothingKnownYet) {
    return (
      <View style={styles.screen}>
        <ScreenHeader title="Inbox" actions={actions} />
        <SkeletonList />
      </View>
    );
  }

  const { groups } = data;

  return (
    <View style={styles.screen}>
      <ScreenHeader title="Inbox" actions={actions} />
      {count === 0 ? (
        <SurfaceEmpty
          title="You’re all caught up"
          body="Nothing is waiting on you right now."
        />
      ) : (
        <ScrollView
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => void load()}
              tintColor={Theme.textMuted}
            />
          }
        >
          {failure ? (
            <Text style={styles.staleNotice}>
              Showing what was last loaded — couldn’t refresh just now.
            </Text>
          ) : null}

          <Group title="Needs your input" items={groups.tasks} unreadById={unreadById} />
          <Group
            title="Notifications"
            items={groups.notifications}
            unreadById={unreadById}
            onPressItem={onReadNotification}
          />
          <Group title="Failed runs" items={groups.runs} unreadById={unreadById} />

          {__DEV__ ? <DateParsingProof /> : null}
        </ScrollView>
      )}
    </View>
  );
}

type Item = InboxData['groups']['tasks'][number];

function Group({
  title,
  items,
  unreadById,
  onPressItem,
}: {
  title: string;
  items: readonly Item[];
  unreadById: Map<string, boolean>;
  onPressItem?: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <View style={styles.group}>
      <Text style={styles.groupLabel}>
        {title.toUpperCase()}  {items.length}
      </Text>
      {items.map((item) => (
        <InboxRow
          key={item.id}
          title={item.title}
          reason={item.detail}
          age={timeAgo(item.timestamp)}
          // Tasks and runs are unattended by definition — they are on this
          // screen precisely because they are still stuck or still failed.
          // Only a notification has a read state of its own.
          unread={unreadById.has(item.id) ? Boolean(unreadById.get(item.id)) : true}
          onPress={onPressItem ? () => onPressItem(item.id) : undefined}
        />
      ))}
    </View>
  );
}

/**
 * Dev-only, and the reason it exists is the whole point of the exercise: the
 * shared module ranks on `Date.parse`, this backend emits two different date
 * shapes, and the Swift port silently returned nil for both. "Hermes
 * probably handles it" is the same assumption that produced that bug, so the
 * answer is rendered where it can be read off a screenshot.
 */
function DateParsingProof() {
  const report = dateParsingReport();
  return (
    <View style={styles.proof}>
      <Text style={styles.proofTitle}>SHARED-MODULE DATE PARSE (dev only)</Text>
      {report.map((line) => (
        <Text key={line.label} style={styles.proofLine}>
          {line.ok ? '✓' : '✗'} {line.label}
        </Text>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Theme.bgPage },
  list: { paddingBottom: 120 },
  group: { marginBottom: Space.x5 },
  groupLabel: {
    ...Type.sectionLabel,
    color: Theme.textMuted,
    paddingHorizontal: Space.x5,
    paddingBottom: Space.x2,
  },
  staleNotice: {
    ...Type.rowSubtitle,
    color: Theme.warning,
    paddingHorizontal: Space.x5,
    paddingBottom: Space.x3,
  },
  proof: {
    marginHorizontal: Space.x5,
    padding: Space.x3,
    borderRadius: 10,
    backgroundColor: Theme.bgInset,
    gap: 3,
  },
  proofTitle: { ...Type.sectionLabel, color: Theme.textMuted, fontSize: 10 },
  proofLine: { ...Type.rowSubtitle, color: Theme.textSecondary, fontSize: 12 },
});

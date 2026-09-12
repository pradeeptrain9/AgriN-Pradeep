import React, { useCallback } from 'react';
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';

import { Button } from '../../components/Button';
import { StatusPill } from '../../components/StatusPill';
import { colors, radius, spacing, type } from '../../constants/theme';
import { loadAdvisory, outboxCount, type CachedField } from '../../db';
import { useDeviceTier } from '../../hooks/useDeviceTier';
import { useFocusEffect } from '@react-navigation/native';

import { drainOutbox } from '../../services/sync';
import { useFieldStore } from '../../store/fieldSlice';
import type { Field } from '../../types';

export const FieldListScreen: React.FC<{ navigation: any }> = ({ navigation }) => {
  const { fields, isLoading, isStale, loadCached, refresh } = useFieldStore();
  const tier = useDeviceTier();
  const [pending, setPending] = React.useState(0);

  // Runs on every focus, not just on mount. A plain useEffect left the status
  // pills frozen at whatever they were when the app started: open a field, see
  // "Needs attention", come back, and the card still reads "Not known". The
  // list is the screen a farmer checks first, so it is the one that must not
  // lie about which field needs walking out to.
  //
  // It is also where the outbox drains, so returning here after regaining
  // signal is what sends a field walked offline.
  useFocusEffect(
    useCallback(() => {
      loadCached();          // paint from the mirror first
      refresh();             // then reconcile if there is signal
      // Re-read the mirror once the queue drains. Sending a field rewrites its
      // local id and clears its pending flag, and without this the card still
      // reads "Not sent yet" after it has arrived -- which looks exactly like
      // the failure it is not, to a farmer who has just walked back into
      // signal and is checking whether their work survived.
      drainOutbox().then((result) => {
        setPending(outboxCount());
        // Pull from the node, not just re-read the mirror. Sending a queued
        // crop or soil card changes the field on the server, and the local
        // copy predates that -- so a re-read still shows "no crop set" for a
        // field whose crop has just arrived. Only a refresh reconciles it.
        if (result.sent > 0) refresh();
      });
    }, [loadCached, refresh]),
  );

  const renderItem = useCallback(
    ({ item }: { item: CachedField }) => {
      const cached = loadAdvisory(item.id);
      const severity = cached?.payload.health?.severity;
      const irrigateNow = cached?.payload.irrigation?.irrigate_now;

      return (
        <Pressable
          style={styles.card}
          onPress={() => navigation.navigate('FieldDetail', { fieldId: item.id })}
          accessibilityRole="button"
          accessibilityLabel={`${item.name}, ${item.area_ha.toFixed(2)} hectares`}
        >
          <View style={styles.cardHeader}>
            <Text style={styles.cardTitle} numberOfLines={1}>{item.name}</Text>
            {/* Not sent yet is a different thing from unhealthy, and a farmer
                who walked a boundary with no signal needs to see that the walk
                was not wasted. */}
            {item.pending ? (
              <Text style={styles.pendingPill}>Not sent yet</Text>
            ) : (
              <StatusPill severity={severity} />
            )}
          </View>
          <Text style={styles.cardMeta}>
            {item.area_ha.toFixed(2)} ha
            {/* The area is a multiplier on every fertiliser and water figure
                the advisory gives, so how it was obtained belongs next to it,
                not on a details screen nobody opens. */}
            {item.source === 'drawn' ? ' (drawn)' : ''}
            {item.crop ? ` · ${item.crop.crop_code.replace(/_/g, ' ')}` : ' · no crop set'}
          </Text>
          {irrigateNow ? <Text style={styles.urgent}>Needs water today</Text> : null}
          {cached && cached.ageHours > 24 ? (
            <Text style={styles.stale}>
              Last updated {Math.round(cached.ageHours / 24)} day(s) ago
            </Text>
          ) : null}
        </Pressable>
      );
    },
    [navigation],
  );

  return (
    <View style={styles.container}>
      {isStale ? (
        <Text style={styles.offlineBanner}>
          No internet. Showing what is saved on your phone.
        </Text>
      ) : null}
      {pending > 0 ? (
        <Text style={styles.offlineBanner}>
          {pending} item(s) waiting to be sent when you are online.
        </Text>
      ) : null}

      <FlatList
        data={fields}
        keyExtractor={(item) => item.id}
        renderItem={renderItem}
        windowSize={tier.listWindowSize}
        initialNumToRender={tier.tier === 'low' ? 4 : 10}
        removeClippedSubviews
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl refreshing={isLoading} onRefresh={refresh} tintColor={colors.primary} />
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>No fields yet</Text>
            <Text style={styles.emptyBody}>
              Walk around the edge of a field and AgriN will map it for you, or
              mark its corners on the map if you cannot get there.
              {'\n\n'}
              You do not have to map anything to check a leaf for disease.
            </Text>
          </View>
        }
      />

      <View style={styles.footer}>
        <Button
          label="Walk a new field"
          icon="+"
          onPress={() => navigation.navigate('MapField')}
        />
        {/* Deliberately second and deliberately quieter. Walking produces a
            boundary someone stood on; drawing produces one someone believes
            in. Both are offered because walking is often impossible -- leased
            land, a plot across a canal, a block too large to walk today -- but
            the better one leads. */}
        <View style={styles.footerSpacer} />
        <Button
          label="Draw it on the map instead"
          icon="✎"
          variant="secondary"
          onPress={() => navigation.navigate('DrawField')}
        />
        <View style={styles.footerSpacer} />
        <Button
          label="Check a leaf for disease"
          icon="📷"
          variant="secondary"
          onPress={() => navigation.navigate('Scan', {})}
        />
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  list: { padding: spacing.md, paddingBottom: spacing.xl },
  card: {
    backgroundColor: colors.surface, borderRadius: radius.md,
    padding: spacing.md, marginBottom: spacing.sm,
  },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  pendingPill: {
    ...type.label,
    color: colors.textMuted,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  cardTitle: { ...type.title, color: colors.text, flex: 1, marginRight: spacing.sm },
  cardMeta: { ...type.body, color: colors.textMuted, marginTop: spacing.xs },
  urgent: { ...type.label, color: colors.water, marginTop: spacing.sm },
  stale: { ...type.caption, color: colors.textMuted, marginTop: spacing.xs },
  offlineBanner: {
    ...type.caption, color: colors.text, backgroundColor: '#FFF3CD',
    padding: spacing.sm, textAlign: 'center',
  },
  empty: { padding: spacing.xl, alignItems: 'center' },
  emptyTitle: { ...type.title, color: colors.text },
  emptyBody: { ...type.body, color: colors.textMuted, textAlign: 'center', marginTop: spacing.sm },
  footerSpacer: { height: spacing.sm },
  footer: { padding: spacing.md, borderTopWidth: 1, borderTopColor: colors.border },
});

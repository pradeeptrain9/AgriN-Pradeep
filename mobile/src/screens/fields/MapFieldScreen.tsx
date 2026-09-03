/**
 * Walk-the-boundary field mapping screen.
 *
 * The whole interaction is three states -- ready, walking, review -- because the
 * user is outdoors, one-handed, possibly in the rain. Live GPS accuracy is shown
 * the whole time: a farmer who knows the fix is poor will wait, whereas one
 * shown nothing just assumes the app is broken.
 */

import React, { useState } from 'react';
import { Alert, StyleSheet, Text, TextInput, View } from 'react-native';

import { Button } from '../../components/Button';
import { BoundaryTrace } from '../../components/map/BoundaryTrace';
import { FieldMapView } from '../../components/map/FieldMapView';
import { FIELD_LIMITS, GPS_CONFIG } from '../../constants/config';
import { colors, radius, spacing, type } from '../../constants/theme';
import { enqueue, insertLocalField, newLocalId } from '../../db';
import { useFieldBoundary } from '../../hooks/useFieldBoundary';
import { createField, isOffline, readableError } from '../../services/api';
import { downloadFieldTiles } from '../../services/offlineTiles';
import { useFieldStore } from '../../store/fieldSlice';
import { polygonCentroid, trackToPolygon } from '../../utils/geo';

const PROBLEM_TEXT: Record<string, string> = {
  too_few_points: 'Walk a little further around the edge.',
  not_a_shape: 'The path does not close into a field yet.',
  too_small: `This field is smaller than ${FIELD_LIMITS.minAreaHa} hectares. Satellite pictures are 10 metres wide, so a plot this small cannot be watched from space.`,
  too_large: 'That area is too large to be one field.',
  crosses_itself: 'The path crosses over itself. Walk the outside edge in one loop.',
};

export const MapFieldScreen: React.FC<{ navigation: any }> = ({ navigation }) => {
  const {
    isWalking, coordinates, perimeterM, distanceToStartM, lastAccuracyM,
    rejectedForAccuracy, canClose, validation, startWalking, stopWalking, cancel, undoLast,
  } = useFieldBoundary();

  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const upsert = useFieldStore((s) => s.upsert);

  const reviewing = !isWalking && coordinates.length > 0;
  const accuracyPoor =
    lastAccuracyM != null && lastAccuracyM > GPS_CONFIG.maxAcceptableAccuracyM;

  const save = async () => {
    const polygon = trackToPolygon(coordinates);
    if (!polygon || !validation.valid) return;

    setSaving(true);
    const fieldName = name.trim() || 'My field';
    try {
      const field = await createField(fieldName, polygon);
      upsert(field);
      // Cache the basemap now, while there is still signal. The next visit to
      // this field will almost certainly have none.
      downloadFieldTiles(field.id, polygon).catch(() => {});
      await cancel();
      navigation.replace('FieldDetail', { fieldId: field.id });
    } catch (error) {
      if (isOffline(error)) {
        // Mapping is the one thing that must never be lost to a dead signal.
        //
        // Storing it in the outbox alone is not enough: the field would be
        // invisible in the list, so the farmer could not set a crop on it,
        // could not scan a leaf against it, and would reasonably assume the
        // walk failed and do it again. Give it a local id now and treat it as
        // a real field; the drain rewrites that id once a node accepts it.
        const localId = newLocalId();
        insertLocalField({
          id: localId,
          name: fieldName,
          area_ha: validation.areaHa,
          centroid: polygonCentroid(polygon),
          geometry: polygon,
        });
        enqueue('create_field', {
          local_id: localId, name: fieldName, geometry: polygon,
        });
        await cancel();
        Alert.alert(
          'Saved on your phone',
          'You have no internet right now. The field is on your phone and you can carry on using it. It will be sent to the node when you are back online.',
          [{ text: 'OK', onPress: () => navigation.replace('FieldDetail', { fieldId: localId }) }],
        );
      } else {
        Alert.alert('Could not save', readableError(error, 'Please try again.'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.stats}>
        <Stat label="Points" value={String(coordinates.length)} />
        <Stat label="Walked" value={`${Math.round(perimeterM)} m`} />
        <Stat
          label="Area"
          value={validation.areaHa > 0 ? `${validation.areaHa.toFixed(2)} ha` : '—'}
        />
      </View>

      <View style={styles.gpsRow}>
        <Text style={[styles.gpsText, accuracyPoor && styles.gpsPoor]}>
          {lastAccuracyM == null
            ? 'Finding GPS…'
            : `GPS accurate to ${Math.round(lastAccuracyM)} m`}
        </Text>
        {rejectedForAccuracy > 0 ? (
          <Text style={styles.gpsHint}>
            {rejectedForAccuracy} weak readings skipped. Stand still a moment under open sky.
          </Text>
        ) : null}
      </View>

      <View style={styles.canvas}>
        {/* The map is an enhancement. If no tile ever loads, the trace still
            draws over the background and the walk completes normally. */}
        <FieldMapView
          followUser={isWalking}
          centerCoordinate={
            !isWalking && coordinates.length > 0
              ? [coordinates[0]!.longitude, coordinates[0]!.latitude]
              : undefined
          }
        >
          <BoundaryTrace coordinates={coordinates} canClose={canClose} />
        </FieldMapView>

        <View pointerEvents="none" style={styles.overlay}>
          <Text style={styles.overlayText}>
            {isWalking
              ? 'Walk along the edge of your field.'
              : reviewing
                ? 'Check the shape, then save.'
                : 'Stand at one corner and press Start.'}
          </Text>
          {isWalking && coordinates.length >= FIELD_LIMITS.minPoints ? (
            <Text style={[styles.overlayText, canClose && styles.overlayReady]}>
              {canClose
                ? 'You are back at the start. Press Finish.'
                : `${Math.round(distanceToStartM)} m back to your starting corner`}
            </Text>
          ) : null}
        </View>
      </View>

      {reviewing && !validation.valid ? (
        <Text style={styles.problem}>{PROBLEM_TEXT[validation.problem ?? ''] ?? ''}</Text>
      ) : null}

      {reviewing && validation.valid ? (
        <View>
          <Text style={styles.label}>Name this field</Text>
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder="North plot"
            placeholderTextColor={colors.textMuted}
            accessibilityLabel="Field name"
          />
        </View>
      ) : null}

      <View style={styles.actions}>
        {!isWalking && !reviewing ? (
          <Button label="Start walking" icon="▶" onPress={startWalking} />
        ) : null}

        {isWalking ? (
          <>
            <Button
              label={canClose ? 'Finish' : 'Finish anyway'}
              icon="■"
              onPress={stopWalking}
              variant={canClose ? 'primary' : 'secondary'}
            />
            <View style={styles.spacer} />
            <Button label="Undo last point" variant="secondary" onPress={undoLast} />
          </>
        ) : null}

        {reviewing ? (
          <>
            <Button
              label="Save field"
              onPress={save}
              disabled={!validation.valid}
              loading={saving}
            />
            <View style={styles.spacer} />
            <Button label="Start again" variant="secondary" onPress={cancel} />
          </>
        ) : null}
      </View>
    </View>
  );
};

const Stat: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <View style={styles.stat}>
    <Text style={styles.statValue}>{value}</Text>
    <Text style={styles.statLabel}>{label}</Text>
  </View>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, padding: spacing.md },
  stats: { flexDirection: 'row', justifyContent: 'space-between' },
  stat: {
    flex: 1, alignItems: 'center', backgroundColor: colors.surface,
    borderRadius: radius.md, paddingVertical: spacing.md, marginHorizontal: spacing.xs,
  },
  statValue: { ...type.title, color: colors.text },
  statLabel: { ...type.caption, color: colors.textMuted },
  gpsRow: { marginTop: spacing.md },
  gpsText: { ...type.body, color: colors.textMuted },
  gpsPoor: { color: colors.watch, fontWeight: '700' },
  gpsHint: { ...type.caption, color: colors.watch, marginTop: spacing.xs },
  canvas: { flex: 1, marginVertical: spacing.md, borderRadius: radius.md, overflow: 'hidden' },
  overlay: {
    position: 'absolute', top: spacing.sm, left: spacing.sm, right: spacing.sm,
    backgroundColor: 'rgba(255,255,255,0.92)', borderRadius: radius.sm,
    padding: spacing.sm,
  },
  overlayText: { ...type.body, color: colors.text, textAlign: 'center' },
  overlayReady: { color: colors.primary, fontWeight: '700', marginTop: spacing.xs },
  problem: { ...type.body, color: colors.alert, marginBottom: spacing.md },
  label: { ...type.label, color: colors.text, marginBottom: spacing.sm },
  input: {
    ...type.body, color: colors.text, borderWidth: 2, borderColor: colors.border,
    borderRadius: radius.md, paddingHorizontal: spacing.md, minHeight: 56,
    marginBottom: spacing.md,
  },
  actions: { paddingBottom: spacing.md },
  spacer: { height: spacing.sm },
});

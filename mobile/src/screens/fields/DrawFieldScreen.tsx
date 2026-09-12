/**
 * Draw a field boundary by tapping its corners on the map.
 *
 * Walking is the better boundary and stays the first option on the list screen.
 * This exists because walking is not always possible: leased land the farmer is
 * standing off, a plot across a canal, a phone whose GPS cannot see sky, an
 * extension officer entering a farmer's field from the KVK office, or the
 * thirty-hectare block nobody is going to walk the perimeter of today.
 *
 * What a drawn field gives up, and what it does not: the satellite index, the
 * weather, the soil lookup and the whole advisory are computed from
 * coordinates, and coordinates tapped on a basemap are as real as coordinates
 * from a GPS. What is lost is the *evidence* -- a walked corner is somewhere a
 * farmer stood, a drawn corner is somewhere they believe the edge runs, and the
 * basemap has its own registration error on top of that. Since every figure the
 * advisory produces is per hectare, the area multiplies into all of them.
 *
 * So the screen says which one this is, twice: on the way in, and in what it
 * stores. `source: 'drawn'` travels to the node with the polygon.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import {
  Alert, PermissionsAndroid, Platform, Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native';
import Geolocation from 'react-native-geolocation-service';

import { Button } from '../../components/Button';
import { BoundaryTrace } from '../../components/map/BoundaryTrace';
import { FieldMapView } from '../../components/map/FieldMapView';
import { DRAW_LIMITS } from '../../constants/config';
import { colors, radius, spacing, touch, type } from '../../constants/theme';
import { enqueue, insertLocalField, newLocalId } from '../../db';
import { createField, isOffline, readableError } from '../../services/api';
import { downloadFieldTiles } from '../../services/offlineTiles';
import { useFieldStore } from '../../store/fieldSlice';
import type { Coordinate } from '../../types';
import { polygonCentroid, trackToPolygon, validateBoundary } from '../../utils/geo';

/** Tapped corners are deliberate, so nothing is thinned away. See geo.ts. */
const NO_SIMPLIFICATION = 0;

const PROBLEM_TEXT: Record<string, string> = {
  too_few_points: 'Tap at least three corners.',
  not_a_shape: 'These points do not close into a field yet.',
  too_small: `This field is smaller than ${DRAW_LIMITS.minAreaHa} hectares. Satellite pictures are 10 metres wide, so a plot this small cannot be watched from space.`,
  too_large: 'That area is too large to be one field.',
  crosses_itself: 'The edges cross over each other. Tap the corners in order, going around the field one way.',
};

/** Centre of India: the last resort, and a bad one -- see below. */
const INDIA: [number, number] = [78.9629, 20.5937];

export const DrawFieldScreen: React.FC<{ navigation: any }> = ({ navigation }) => {
  const [corners, setCorners] = useState<Coordinate[]>([]);
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [center, setCenter] = useState<[number, number] | null>(null);

  const fields = useFieldStore((s) => s.fields);
  const upsert = useFieldStore((s) => s.upsert);

  // Where to open the map matters more here than anywhere else in the app. The
  // default camera is the centre of India at low zoom, and a farmer who has to
  // pinch their way across a subcontinent to find their own village will give
  // up before they find it. An existing field is the best guess and costs
  // nothing; the phone's position is the next best; the country is the
  // fallback that means "you will have to search".
  useEffect(() => {
    const existing = fields.find((f) => f.centroid?.[0] != null);
    if (existing) {
      setCenter([existing.centroid[0], existing.centroid[1]]);
      return;
    }

    let cancelled = false;
    const locate = async () => {
      try {
        if (Platform.OS === 'android') {
          const granted = await PermissionsAndroid.request(
            PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION!,
            {
              title: 'Show your area on the map',
              message:
                'AgriN can open the map where you are, so you do not have to '
                + 'search for your village. You can refuse and pan there yourself.',
              buttonPositive: 'Allow',
            },
          );
          if (granted !== PermissionsAndroid.RESULTS.GRANTED) {
            if (!cancelled) setCenter(INDIA);
            return;
          }
        }
        Geolocation.getCurrentPosition(
          (position) => {
            if (!cancelled) {
              setCenter([position.coords.longitude, position.coords.latitude]);
            }
          },
          // No fix, no signal, indoors: the map still opens and still works.
          () => { if (!cancelled) setCenter(INDIA); },
          { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 },
        );
      } catch {
        if (!cancelled) setCenter(INDIA);
      }
    };
    locate();
    return () => { cancelled = true; };
  }, [fields]);

  const validation = useMemo(
    () => validateBoundary(corners, DRAW_LIMITS, NO_SIMPLIFICATION),
    [corners],
  );

  const addCorner = useCallback(([longitude, latitude]: [number, number]) => {
    // `accuracy` is deliberately left undefined. A walked point carries the
    // GPS's own estimate of how wrong it might be; a tapped one has no such
    // number, and inventing a good-looking one would be the exact confusion
    // this screen exists to avoid.
    setCorners((existing) => [
      ...existing, { latitude, longitude, timestamp: Date.now() },
    ]);
  }, []);

  const undo = useCallback(() => setCorners((c) => c.slice(0, -1)), []);
  const clear = useCallback(() => setCorners([]), []);

  // Leaving with corners on the map throws them away, and the back gesture is
  // one stray swipe from the edge of a screen the farmer is tapping all over.
  // The walk path survives this because the track lives in a store; these
  // corners live in this component and die with it. Losing a boundary someone
  // placed corner by corner, silently, is exactly what teaches a person not to
  // trust an app with the next one.
  //
  // `saved` suppresses the prompt for the navigation the save itself performs.
  const saved = useRef(false);
  useFocusEffect(
    useCallback(() => {
      const unsubscribe = navigation.addListener('beforeRemove', (event: any) => {
        if (corners.length === 0 || saved.current) return;
        event.preventDefault();
        Alert.alert(
          'Leave without saving?',
          `You have marked ${corners.length} corner(s). Leaving now loses them.`,
          [
            { text: 'Keep marking', style: 'cancel' },
            {
              text: 'Leave',
              style: 'destructive',
              onPress: () => navigation.dispatch(event.data.action),
            },
          ],
        );
      });
      return unsubscribe;
    }, [navigation, corners.length]),
  );

  const save = async () => {
    const polygon = trackToPolygon(corners, NO_SIMPLIFICATION);
    if (!polygon || !validation.valid) return;

    setSaving(true);
    const fieldName = name.trim() || 'My field';
    try {
      const field = await createField(fieldName, polygon, 'drawn');
      upsert(field);
      downloadFieldTiles(field.id, polygon).catch(() => {});
      saved.current = true;
      navigation.replace('FieldDetail', { fieldId: field.id });
    } catch (error) {
      if (isOffline(error)) {
        // Same offline path as the walk. A boundary drawn with no signal is
        // still work the farmer did, and losing it would teach them not to
        // trust the app with anything else.
        const localId = newLocalId();
        insertLocalField({
          id: localId,
          name: fieldName,
          area_ha: validation.areaHa,
          centroid: polygonCentroid(polygon),
          geometry: polygon,
          source: 'drawn',
        });
        enqueue('create_field', {
          local_id: localId, name: fieldName, geometry: polygon, source: 'drawn',
        });
        Alert.alert(
          'Saved on your phone',
          'You have no internet right now. The field is on your phone and you '
          + 'can carry on using it. It will be sent to the node when you are '
          + 'back online.',
          [{
            text: 'OK',
            onPress: () => {
              saved.current = true;
              navigation.replace('FieldDetail', { fieldId: localId });
            },
          }],
        );
      } else {
        Alert.alert('Could not save', readableError(error, 'Please try again.'));
      }
    } finally {
      setSaving(false);
    }
  };

  const ready = validation.valid;
  const showProblem = corners.length >= DRAW_LIMITS.minPoints && !ready;

  return (
    <View style={styles.container}>
      {/* Said before the first tap, not after the save. A farmer who thinks
          they are recording a survey should find out here. */}
      <Text style={styles.notice}>
        You are marking this field on the map, not walking it. The weather and
        satellite advice will be real for these coordinates, but the size will
        only be as accurate as your corners.
      </Text>

      <View style={styles.stats}>
        <Stat label="corners" value={String(corners.length)} />
        <Stat
          label="hectares"
          value={validation.areaHa > 0 ? validation.areaHa.toFixed(2) : '—'}
        />
      </View>

      <View style={styles.canvas}>
        <FieldMapView
          centerCoordinate={center ?? INDIA}
          zoom={center && center !== INDIA ? 16 : undefined}
          onPress={addCorner}
        >
          <BoundaryTrace coordinates={corners} canClose={ready} vertices closed />
        </FieldMapView>

        {/* Both the instruction and the problem live inside the map overlay,
            which is absolutely positioned and therefore cannot move anything.
            See the layout note above the styles. */}
        <View pointerEvents="none" style={styles.overlay}>
          <Text style={[styles.overlayText, showProblem && styles.overlayProblem]}>
            {showProblem
              ? PROBLEM_TEXT[validation.problem ?? ''] ?? ''
              : corners.length === 0
                ? 'Find your field, then tap each corner.'
                : corners.length < DRAW_LIMITS.minPoints
                  ? `Tap ${DRAW_LIMITS.minPoints - corners.length} more corner(s).`
                  : 'Check the shape against your field, then save.'}
          </Text>
        </View>

        {/* Undo sits on the map, within thumb reach of where the tapping
            happens. A misplaced corner is the common mistake and the fix
            should not be at the other end of the screen. */}
        <Pressable
          style={[styles.undo, corners.length === 0 && styles.undoDisabled]}
          onPress={undo}
          disabled={corners.length === 0}
          accessibilityRole="button"
          accessibilityLabel="Undo last corner"
        >
          <Text style={styles.undoText}>↶ Undo corner</Text>
        </Pressable>
      </View>

      <Text style={styles.label}>Name this field</Text>
      <TextInput
        style={styles.input}
        value={name}
        onChangeText={setName}
        placeholder="North plot"
        placeholderTextColor={colors.textMuted}
        accessibilityLabel="Field name"
      />

      <View style={styles.actions}>
        <Button label="Save field" onPress={save} disabled={!ready} loading={saving} />
        <View style={styles.spacer} />
        <Button
          label="Start again"
          variant="secondary"
          onPress={clear}
          disabled={corners.length === 0}
        />
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

/**
 * Nothing below the map may appear, disappear or resize while corners are being
 * tapped.
 *
 * This is not tidiness. The first build showed the name field only once the
 * shape was valid, so the third tap made it appear, which pushed the map up and
 * slid "Save field" under where the farmer's finger was already going. The
 * fourth tap saved a three-corner field instead of placing a corner -- no
 * confirmation, no way back, and the boundary was wrong.
 *
 * So every control is rendered from the start and disabled rather than hidden,
 * and the instruction and the problem message share one slot inside the map
 * overlay, which is absolutely positioned and cannot shift anything.
 */
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg, paddingBottom: spacing.md },
  // Caption, not body, and tighter. It is a standing disclaimer rather than an
  // instruction, and at body size it was taking four lines off the top of the
  // map -- the one element on this screen a farmer actually has to work in.
  notice: {
    ...type.caption,
    color: colors.text,
    backgroundColor: '#FFF3CD',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  // Corners and area on one line instead of two cards. They are a readout to
  // glance at, not a section of the page.
  stats: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline',
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
  },
  stat: { flexDirection: 'row', alignItems: 'baseline' },
  statValue: { ...type.title, color: colors.text },
  statLabel: { ...type.caption, color: colors.textMuted, marginLeft: spacing.xs },
  // Full bleed and given every pixel the rest of the screen does not need.
  // The map is where the work happens; everything else is a caption on it.
  canvas: { flex: 1, overflow: 'hidden' },
  overlay: {
    position: 'absolute', top: spacing.sm, left: spacing.sm, right: spacing.sm,
    backgroundColor: 'rgba(255,255,255,0.92)', borderRadius: radius.sm,
    padding: spacing.sm,
  },
  overlayText: { ...type.body, color: colors.text, textAlign: 'center' },
  overlayProblem: { color: colors.alert, fontWeight: '700' },
  undo: {
    position: 'absolute', bottom: spacing.md, left: spacing.md,
    backgroundColor: colors.surface, borderRadius: radius.md,
    borderWidth: 2, borderColor: colors.border,
    paddingHorizontal: spacing.md, justifyContent: 'center',
    minHeight: touch.minTarget,
  },
  undoDisabled: { opacity: 0.4 },
  undoText: { ...type.body, color: colors.text, fontWeight: '600' },
  label: {
    ...type.caption, color: colors.textMuted,
    paddingHorizontal: spacing.md, marginTop: spacing.sm,
  },
  input: {
    ...type.body, color: colors.text, borderWidth: 2, borderColor: colors.border,
    borderRadius: radius.md, paddingHorizontal: spacing.md, minHeight: 52,
    marginHorizontal: spacing.md, marginTop: spacing.xs,
  },
  actions: { paddingHorizontal: spacing.md, paddingTop: spacing.sm },
  spacer: { height: spacing.sm },
});

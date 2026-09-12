/**
 * Tell the app what is growing in this field.
 *
 * Without a crop and a sowing date the advisory engine has nothing to work
 * with: every recommendation is a function of the crop's stage, which is a
 * function of days after sowing. The backend returns `status: no_crop` until
 * this is set, so this screen is the gate on the whole product.
 *
 * Date entry is deliberately not a calendar widget. A farmer knows "about three
 * weeks ago" far more readily than a date, and a picker is a fiddly target on a
 * cheap phone in sunlight. Relative choices come first; the exact date is there
 * for anyone who wants it.
 */

import React, { useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native';

import { Button } from '../../components/Button';
import { colors, radius, spacing, touch, type } from '../../constants/theme';
import { enqueue, loadCrops, saveCrops } from '../../db';
import type { CropOption } from '../../types';
import { isOffline, listCrops, readableError, setCrop } from '../../services/api';
import { useFieldStore } from '../../store/fieldSlice';

const RELATIVE_CHOICES = [
  { label: 'Today', days: 0 },
  { label: '1 week ago', days: 7 },
  { label: '2 weeks ago', days: 14 },
  { label: '1 month ago', days: 30 },
  { label: '2 months ago', days: 60 },
  { label: '3 months ago', days: 90 },
];

const isoDaysAgo = (days: number): string => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
};

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

export const CropScreen: React.FC<{ route: any; navigation: any }> = ({ route, navigation }) => {
  const { fieldId } = route.params;
  const refresh = useFieldStore((s) => s.refresh);

  const [crops, setCrops] = useState<CropOption[]>([]);
  const [loading, setLoading] = useState(true);
  // Arriving from a suggestion, the crop is already chosen -- so the farmer
  // lands on the sowing date rather than hunting the list for the name they
  // just tapped. Still changeable: the suggestion was a suggestion.
  const [selected, setSelected] = useState<string | null>(
    route.params?.preselect ?? null,
  );
  const [previous, setPrevious] = useState<string | null>(null);
  const [sowing, setSowing] = useState(isoDaysAgo(14));
  const [choice, setChoice] = useState(14);
  const [manualDate, setManualDate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Paint from the mirror first. The crop list is static reference data, and
    // a field walked with no signal has to be able to reach this screen and
    // finish -- the pending-field screen promises exactly that.
    const cached = loadCrops<CropOption>();
    if (cached.length > 0) {
      setCrops(cached);
      setLoading(false);
    }

    listCrops()
      .then((list) => {
        setCrops(list);
        saveCrops(list);
        setError(null);
      })
      .catch(() => {
        if (cached.length === 0) {
          setError(
            'The crop list has not been downloaded yet, and there is no ' +
              'internet. Connect once and it will be kept on the phone.',
          );
        }
      })
      .finally(() => setLoading(false));
  }, []);

  const chosen = useMemo(
    () => crops.find((c) => c.code === selected) ?? null,
    [crops, selected],
  );

  const dateValid = DATE_PATTERN.test(sowing) && !Number.isNaN(Date.parse(sowing));
  const inFuture = dateValid && new Date(sowing) > new Date();

  const save = async () => {
    if (!selected || !dateValid || inFuture) return;
    setBusy(true);
    setError(null);
    const body = { crop_code: selected, sowing_date: sowing, previous_crop: previous };
    try {
      await setCrop(fieldId, body);
      await refresh();
      navigation.goBack();
    } catch (err) {
      if (isOffline(err)) {
        enqueue('set_crop', { field_id: fieldId, body });
        Alert.alert(
          'Saved on your phone',
          'No internet right now. This will be sent when you are back online.',
          [{ text: 'OK', onPress: () => navigation.goBack() }],
        );
      } else {
        setError(readableError(err, 'Could not save the crop.'));
      }
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.section}>What are you growing?</Text>
      <View style={styles.grid}>
        {crops.map((crop) => {
          const active = crop.code === selected;
          return (
            <Pressable
              key={crop.code}
              onPress={() => setSelected(crop.code)}
              accessibilityRole="radio"
              accessibilityState={{ selected: active }}
              accessibilityLabel={crop.label}
              style={[styles.chip, active && styles.chipActive]}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>
                {crop.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {chosen ? (
        <Text style={styles.hint}>
          {chosen.label} takes about {chosen.season_days} days from sowing to harvest.
          {chosen.fixes_nitrogen
            ? ' It makes its own nitrogen, so it needs very little fertiliser.'
            : ''}
        </Text>
      ) : null}

      <Text style={styles.section}>When did you sow it?</Text>
      <View style={styles.grid}>
        {RELATIVE_CHOICES.map((option) => {
          const active = !manualDate && choice === option.days;
          return (
            <Pressable
              key={option.label}
              onPress={() => {
                setChoice(option.days);
                setSowing(isoDaysAgo(option.days));
                setManualDate(false);
              }}
              accessibilityRole="radio"
              accessibilityState={{ selected: active }}
              style={[styles.chip, active && styles.chipActive]}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>
                {option.label}
              </Text>
            </Pressable>
          );
        })}
        <Pressable
          onPress={() => setManualDate(true)}
          accessibilityRole="radio"
          accessibilityState={{ selected: manualDate }}
          style={[styles.chip, manualDate && styles.chipActive]}
        >
          <Text style={[styles.chipText, manualDate && styles.chipTextActive]}>
            Another date
          </Text>
        </Pressable>
      </View>

      {manualDate ? (
        <TextInput
          style={styles.input}
          value={sowing}
          onChangeText={setSowing}
          placeholder="YYYY-MM-DD"
          placeholderTextColor={colors.textMuted}
          keyboardType="numbers-and-punctuation"
          accessibilityLabel="Sowing date, year month day"
        />
      ) : (
        <Text style={styles.hint}>Sowing date: {sowing}</Text>
      )}
      {manualDate && !dateValid ? (
        <Text style={styles.error}>Write the date as YYYY-MM-DD, for example 2026-06-20.</Text>
      ) : null}
      {inFuture ? (
        <Text style={styles.error}>That date is in the future.</Text>
      ) : null}

      <Text style={styles.section}>What grew here last season?</Text>
      <Text style={styles.hint}>
        This matters: a legume leaves nitrogen behind, and repeating the same crop
        lets pests build up.
      </Text>
      <View style={styles.grid}>
        <Pressable
          onPress={() => setPrevious(null)}
          accessibilityRole="radio"
          accessibilityState={{ selected: previous === null }}
          style={[styles.chip, previous === null && styles.chipActive]}
        >
          <Text style={[styles.chipText, previous === null && styles.chipTextActive]}>
            Do not know
          </Text>
        </Pressable>
        {crops.map((crop) => {
          const active = crop.code === previous;
          return (
            <Pressable
              key={`prev-${crop.code}`}
              onPress={() => setPrevious(crop.code)}
              accessibilityRole="radio"
              accessibilityState={{ selected: active }}
              style={[styles.chip, active && styles.chipActive]}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>
                {crop.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={styles.footer}>
        <Button
          label="Save crop"
          onPress={save}
          loading={busy}
          disabled={!selected || !dateValid || inFuture}
        />
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  centered: { flex: 1, justifyContent: 'center', backgroundColor: colors.bg },
  section: { ...type.title, color: colors.text, marginTop: spacing.lg, marginBottom: spacing.sm },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  chip: {
    minHeight: touch.minTarget, justifyContent: 'center',
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm,
    borderRadius: radius.md, borderWidth: 2, borderColor: colors.border,
    backgroundColor: colors.surface, marginRight: spacing.sm, marginBottom: spacing.sm,
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primaryDark },
  chipText: { ...type.body, color: colors.text },
  chipTextActive: { color: colors.onPrimary, fontWeight: '700' },
  hint: { ...type.caption, color: colors.textMuted, marginBottom: spacing.sm },
  input: {
    ...type.body, color: colors.text, borderWidth: 2, borderColor: colors.border,
    borderRadius: radius.md, paddingHorizontal: spacing.md, minHeight: touch.minTarget,
  },
  error: { ...type.body, color: colors.alert, marginTop: spacing.sm },
  footer: { marginTop: spacing.lg },
});

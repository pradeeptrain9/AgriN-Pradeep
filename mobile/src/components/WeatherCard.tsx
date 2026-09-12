/**
 * The weather this field is actually getting.
 *
 * The node has held this all along -- roughly 200 days of history and two
 * weeks of forecast per field, temperature included -- and used it to decide
 * irrigation timing without ever showing a farmer any of it. So the app said
 * "no watering needed" and gave no way to see why.
 *
 * Measured days and predicted days are marked differently and never merged.
 * A farmer deciding whether to irrigate on the strength of "8 mm on Wednesday"
 * should know that Wednesday has not happened yet.
 */

import React from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { colors, radius, spacing, type } from '../constants/theme';
import type { WeatherDay } from '../types';

interface Props {
  daily: WeatherDay[];
  rainAheadMm: number;
  loading?: boolean;
  ageHours?: number | null;
  gaps?: string[];
}

const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/** "Today", "Tomorrow", else a weekday name a farmer can act on. */
const dayLabel = (iso: string): string => {
  const parts = iso.split('-').map(Number);
  const date = new Date(parts[0]!, (parts[1] ?? 1) - 1, parts[2] ?? 1);
  if (Number.isNaN(date.getTime())) return iso;

  const today = new Date();
  const midnight = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round(
    (midnight(date) - midnight(today)) / (24 * 60 * 60 * 1000),
  );

  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Tomorrow';
  if (diffDays === -1) return 'Yesterday';
  return DAY_NAMES[date.getDay()] ?? iso;
};

const temperature = (value: number | null): string =>
  value === null ? '—' : `${Math.round(value)}°`;

const rain = (value: number | null): string =>
  value === null ? '—' : value < 0.1 ? '–' : `${value.toFixed(1)}`;

export const WeatherCard: React.FC<Props> = ({
  daily, rainAheadMm, loading, ageHours, gaps,
}) => {
  if (loading && daily.length === 0) {
    return (
      <View style={styles.card}>
        <Text style={styles.title}>Weather</Text>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  if (daily.length === 0) {
    return (
      <View style={styles.card}>
        <Text style={styles.title}>Weather</Text>
        <Text style={styles.empty}>
          {gaps?.[0]
            ?? 'No weather for this field yet. Tap Update from satellite.'}
        </Text>
      </View>
    );
  }

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.title}>Weather</Text>
        {ageHours != null && ageHours > 24 ? (
          <Text style={styles.stale}>
            {Math.round(ageHours / 24)} day(s) old
          </Text>
        ) : null}
      </View>

      <Text style={styles.summary}>
        {rainAheadMm >= 0.1
          ? `About ${rainAheadMm} mm of rain expected in the days ahead.`
          : 'No rain expected in the days ahead.'}
      </Text>

      {/* One row per day, not eight columns across.
          Columns were the first attempt and they broke on a real phone: the
          type scale here is deliberately large so a farmer can read it outdoors
          at arm's length, and eight of those across 360dp leaves 40dp each.
          "Tomorrow" wrapped to three lines, every column ended up a different
          height, and the grid stopped lining up at all. Shrinking the text
          would have fixed the layout by breaking the thing the layout is for. */}
      <View style={styles.days}>
        {daily.map((day) => (
          <View
            key={day.day}
            style={[styles.dayRow, day.kind === 'observed' && styles.dayObserved]}
            accessible
            accessibilityLabel={
              `${dayLabel(day.day)}, ${day.kind === 'observed' ? 'measured' : 'expected'}, `
              + `rain ${rain(day.precip_mm)} millimetres, `
              + `high ${temperature(day.tmax_c)}, low ${temperature(day.tmin_c)}`
            }
          >
            <Text style={styles.dayName} numberOfLines={1}>{dayLabel(day.day)}</Text>
            <Text style={styles.dayRain} numberOfLines={1}>
              {rain(day.precip_mm)}<Text style={styles.rainUnit}> mm</Text>
            </Text>
            <Text style={styles.dayTemps} numberOfLines={1}>
              {temperature(day.tmax_c)}
              <Text style={styles.tempMin}> / {temperature(day.tmin_c)}</Text>
            </Text>
          </View>
        ))}
      </View>

      {/* Not decoration: the irrigation advice above leans on these numbers,
          and half of them have not happened yet. */}
      <Text style={styles.legend}>
        Shaded days were measured. The rest is the forecast, which can change.
      </Text>
    </View>
  );
};

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.md,
  },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  title: { ...type.label, color: colors.textMuted, textTransform: 'uppercase' },
  stale: { ...type.label, color: colors.textMuted },
  summary: { ...type.body, color: colors.text, marginTop: spacing.xs },
  empty: { ...type.body, color: colors.textMuted, marginTop: spacing.xs },
  days: { marginTop: spacing.sm },
  dayRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.sm,
    borderRadius: radius.sm,
    // A hairline between rows so eight of them read as a list rather than a
    // block of numbers.
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  dayObserved: { backgroundColor: colors.bg },
  // Widest label is "Yesterday"; fixed so every row's figures line up in a
  // column the eye can run down.
  dayName: { ...type.body, color: colors.text, width: 104 },
  dayRain: { ...type.body, color: colors.text, fontWeight: '700', flex: 1, textAlign: 'right' },
  rainUnit: { ...type.caption, color: colors.textMuted, fontWeight: '400' },
  dayTemps: { ...type.body, color: colors.text, width: 96, textAlign: 'right' },
  tempMin: { color: colors.textMuted },
  legend: { ...type.label, color: colors.textMuted, marginTop: spacing.sm },
});

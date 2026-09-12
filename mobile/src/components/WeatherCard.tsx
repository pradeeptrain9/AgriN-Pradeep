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

      <View style={styles.row}>
        {daily.map((day) => (
          <View
            key={day.day}
            style={[styles.day, day.kind === 'observed' && styles.dayObserved]}
            accessible
            accessibilityLabel={
              `${dayLabel(day.day)}, ${day.kind === 'observed' ? 'measured' : 'expected'}, `
              + `high ${temperature(day.tmax_c)}, low ${temperature(day.tmin_c)}, `
              + `rain ${rain(day.precip_mm)} millimetres`
            }
          >
            <Text style={styles.dayName}>{dayLabel(day.day)}</Text>
            <Text style={styles.rain}>{rain(day.precip_mm)}</Text>
            <Text style={styles.rainUnit}>mm</Text>
            <Text style={styles.temps}>
              {temperature(day.tmax_c)}
              <Text style={styles.tempMin}> {temperature(day.tmin_c)}</Text>
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
  row: { flexDirection: 'row', marginTop: spacing.md },
  day: {
    flex: 1,
    alignItems: 'center',
    paddingVertical: spacing.sm,
    borderRadius: radius.sm,
  },
  dayObserved: { backgroundColor: colors.bg },
  dayName: { ...type.label, color: colors.textMuted },
  rain: { ...type.body, color: colors.text, fontWeight: '700', marginTop: 2 },
  rainUnit: { ...type.label, color: colors.textMuted },
  temps: { ...type.label, color: colors.text, marginTop: 4 },
  tempMin: { color: colors.textMuted },
  legend: { ...type.label, color: colors.textMuted, marginTop: spacing.sm },
});

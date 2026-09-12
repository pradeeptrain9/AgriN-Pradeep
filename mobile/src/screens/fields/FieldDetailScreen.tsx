/**
 * Field health card.
 *
 * Ordered by what the farmer must do, not by what the system computed: today's
 * action first, then the reason, then the season. Gaps and stale data are shown
 * rather than hidden -- a confident-looking card built on a three-week-old
 * satellite image is worse than one that says so.
 */

import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button } from '../../components/Button';
import { FeedbackPrompt } from '../../components/FeedbackPrompt';
import { WeatherCard } from '../../components/WeatherCard';
import { FieldMapView } from '../../components/map/FieldMapView';
import { FieldPolygon } from '../../components/map/FieldPolygon';
import { StatusPill } from '../../components/StatusPill';
import { colors, radius, spacing, type } from '../../constants/theme';
import {
  isLocalId, loadAdvisory, loadFields, loadWeather, saveAdvisory, saveWeather,
} from '../../db';
import { boundsFor } from '../../services/offlineTiles';
import {
  getFieldWeather, getNarratedAdvisory, isOffline, refreshField,
} from '../../services/api';
import { useAuthStore } from '../../store/authSlice';
import type { Advisory, FieldWeather, Narration } from '../../types';

export const FieldDetailScreen: React.FC<{ route: any; navigation: any }> = ({
  route, navigation,
}) => {
  const { fieldId } = route.params;
  const lang = useAuthStore((s) => s.lang);

  const [advisory, setAdvisory] = useState<Advisory | null>(null);
  const [narration, setNarration] = useState<Narration | null>(null);
  const [ageHours, setAgeHours] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  // Geometry comes from the offline mirror, so the map draws with no signal.
  const [field, setField] = useState(
    () => loadFields().find((f) => f.id === fieldId) ?? null,
  );

  const [weather, setWeather] = useState<FieldWeather | null>(null);
  const [weatherAge, setWeatherAge] = useState<number | null>(null);

  const refreshWeather = useCallback(async () => {
    if (isLocalId(fieldId)) return;      // not on the node yet

    // Paint from the mirror first so the card is there instantly and survives
    // a dead signal, then reconcile.
    const cached = loadWeather<FieldWeather>(fieldId);
    if (cached) {
      setWeather(cached.payload);
      setWeatherAge(cached.ageHours);
    }

    try {
      const fresh = await getFieldWeather(fieldId, 7);
      setWeather(fresh);
      setWeatherAge(0);
      saveWeather(fieldId, fresh);
    } catch {
      // Keep whatever the mirror gave us; the card labels its own age.
    }
  }, [fieldId]);

  const load = useCallback(async () => {
    // A field mapped offline has no id the node would recognise, so asking for
    // an advisory can only 404. Wait until the drain gives it a real one.
    if (isLocalId(fieldId)) return;

    const cached = loadAdvisory(fieldId);
    if (cached) {
      setAdvisory(cached.payload);
      setNarration(cached.narration as Narration | null);
      setAgeHours(cached.ageHours);
    }
    try {
      const fresh = await getNarratedAdvisory(fieldId, lang);
      saveAdvisory(fieldId, fresh.advisory, fresh.narration);
      setAdvisory(fresh.advisory);
      setNarration(fresh.narration);
      setAgeHours(0);
    } catch (error) {
      if (!isOffline(error) && !cached) {
        setAdvisory(null);
      }
    }
  }, [fieldId, lang]);

  // Reload whenever this screen comes back into focus. A plain useEffect runs
  // once on mount, so returning from the crop or soil screens left the old
  // advisory on screen -- a farmer who had just entered their crop was still
  // being told to enter their crop.
  useFocusEffect(
    useCallback(() => {
      setField(loadFields().find((f) => f.id === fieldId) ?? null);
      load();
      refreshWeather();
    }, [fieldId, load, refreshWeather]),
  );

  const requestRefresh = async () => {
    setBusy(true);
    try {
      await refreshField(fieldId);
      await load();
    } finally {
      setBusy(false);
    }
  };

  // Mapped with no signal and not yet sent. "Fetch advice" would fail here and
  // read as a broken app, when in fact nothing is wrong and nothing is lost.
  if (isLocalId(fieldId)) {
    return (
      <View style={styles.centered}>
        <Text style={styles.sectionTitle}>{field?.name ?? 'Your field'}</Text>
        <Text style={styles.body}>
          This field is saved on your phone and has not reached the node yet.
          {'\n\n'}
          You can add the crop and take leaf photos now. Everything you enter is
          kept and sent together as soon as you have internet, and the advice
          will be ready then.
        </Text>
        <Button
          label="Add the crop"
          onPress={() => navigation.navigate('Crop', { fieldId })}
        />
      </View>
    );
  }

  if (!advisory) {
    return (
      <View style={styles.centered}>
        <Text style={styles.body}>No advice saved for this field yet.</Text>
        <Button label="Fetch advice" onPress={requestRefresh} loading={busy} />
      </View>
    );
  }

  // The engine returns this until a crop and sowing date exist. Every
  // recommendation is a function of days after sowing, so nothing can be said
  // before then -- make that the one obvious action rather than an error.
  if (advisory.status === 'no_crop') {
    return (
      <View style={styles.centered}>
        <Text style={styles.headline}>What are you growing?</Text>
        <Text style={styles.body}>
          Advice depends on the crop and how many days it has been growing, so
          we cannot say anything useful yet.
          {'\n\n'}
          If the field is empty, we can suggest what suits it — based on the
          rain it actually gets and what grew here last season.
        </Text>
        <View style={{ height: spacing.lg }} />
        {/* The suggestion leads, because a farmer standing on a bare field is
            more often deciding than recording. Both routes end at the same
            crop screen. */}
        <Button
          label="Suggest what to grow"
          onPress={() => navigation.navigate('CropSuggestion', { fieldId })}
        />
        <View style={{ height: spacing.sm }} />
        <Button
          label="I know my crop"
          variant="secondary"
          onPress={() => navigation.navigate('Crop', { fieldId })}
        />
      </View>
    );
  }

  const { crop, health, irrigation, nutrients, soil } = advisory;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {ageHours != null && ageHours > 24 ? (
        <Text style={styles.staleBanner}>
          Saved {Math.round(ageHours / 24)} day(s) ago. Connect to the internet to update.
        </Text>
      ) : null}

      {field?.geometry ? (
        <View style={styles.mapCard}>
          <FieldMapView
            bounds={boundsFor(field.geometry) ?? undefined}
            attributionNote={false}
          >
            <FieldPolygon
              id={field.id}
              geometry={field.geometry}
              severity={advisory.health?.severity}
            />
          </FieldMapView>
        </View>
      ) : null}

      {narration ? (
        <View style={styles.summaryCard}>
          <Text style={styles.summary}>{narration.summary}</Text>
          {!narration.translated ? (
            <Text style={styles.caption}>Shown in English.</Text>
          ) : null}
        </View>
      ) : null}

      {crop ? (
        <Section title="Crop">
          <Text style={styles.body}>
            {crop.label} · {crop.stage} stage · day {crop.days_after_sowing}
          </Text>
          <View style={{ height: spacing.sm }} />
          <Button
            label="Check a leaf for disease"
            icon="&#128247;"
            variant="secondary"
            onPress={() =>
              navigation.navigate('Scan', { cropCode: crop.code, fieldId })
            }
          />
          <View style={{ height: spacing.sm }} />
          <Button
            label="Change crop or sowing date"
            variant="secondary"
            onPress={() => navigation.navigate('Crop', { fieldId })}
          />
        </Section>
      ) : null}

      <Section title="Crop health" right={<StatusPill severity={health?.severity} />}>
        {health?.severity === 'unknown' ? (
          <Text style={styles.body}>
            No clear satellite picture of this field yet, so health cannot be scored.
          </Text>
        ) : (
          <Text style={styles.body}>
            Greenness {health?.latest_ndvi?.toFixed(2) ?? '—'} against{' '}
            {health?.expected_ndvi?.toFixed(2) ?? '—'} expected at this stage.
          </Text>
        )}
        {health?.is_stale && health.days_since_observation != null ? (
          <Text style={styles.warn}>
            Last clear picture was {health.days_since_observation} days ago.
          </Text>
        ) : null}
        {health?.notes?.map((note, i) => (
          <Text key={i} style={styles.note}>• {note}</Text>
        ))}
      </Section>

      {/* Above Water on purpose: the irrigation advice below is computed from
          these numbers, and a farmer told "no watering needed" deserves to see
          the rain it is counting on. */}
      <WeatherCard
        daily={weather?.daily ?? []}
        rainAheadMm={weather?.rain_ahead_mm ?? 0}
        ageHours={weatherAge}
        gaps={weather?.gaps}
      />

      {irrigation ? (
        <Section title="Water">
          {irrigation.irrigate_now ? (
            <Text style={styles.headline}>
              Irrigate today — about {Math.round(irrigation.gross_depth_mm)} mm
            </Text>
          ) : irrigation.forecast_irrigation_date ? (
            <Text style={styles.body}>
              Next watering on {irrigation.forecast_irrigation_date}
              {irrigation.days_until_irrigation != null
                ? ` (in ${irrigation.days_until_irrigation} days)`
                : ''}
            </Text>
          ) : (
            <Text style={styles.body}>No watering needed in the days ahead.</Text>
          )}
          {irrigation.model === 'paddy' && irrigation.water_saving_pct ? (
            <Text style={styles.good}>
              Alternate wetting and drying has saved about{' '}
              {Math.round(irrigation.water_saving_pct)}% of your water so far.
            </Text>
          ) : null}
          {irrigation.notes?.map((note, i) => (
            <Text key={i} style={styles.note}>• {note}</Text>
          ))}
        </Section>
      ) : null}

      {nutrients ? (
        <Section title="Fertiliser">
          <Text style={styles.body}>
            Nitrogen {Math.round(nutrients.n.low_kg_ha)}–{Math.round(nutrients.n.high_kg_ha)} kg/ha,
            phosphate {Math.round(nutrients.p2o5.low_kg_ha)}–{Math.round(nutrients.p2o5.high_kg_ha)},
            potash {Math.round(nutrients.k2o.low_kg_ha)}–{Math.round(nutrients.k2o.high_kg_ha)}
          </Text>
          {nutrients.splits?.map((split, i) => (
            <Text key={i} style={styles.note}>
              • {split.when}: {Math.round(split.n_kg_ha)} kg N/ha
            </Text>
          ))}
          {nutrients.regenerative_actions?.map((action, i) => (
            <Text key={i} style={styles.note}>• {action}</Text>
          ))}
        </Section>
      ) : null}

      {soil ? (
        <Section title="Soil">
          <Text style={styles.body}>
            {soil.texture} · from {soil.source.replace(/_/g, ' ')} ({soil.confidence} confidence)
          </Text>
          <View style={{ height: spacing.sm }} />
          <Button
            label={
              soil.confidence === 'high'
                ? 'Update your soil information'
                : 'Enter your Soil Health Card'
            }
            variant="secondary"
            onPress={() => navigation.navigate('SoilCard', { fieldId })}
          />
        </Section>
      ) : null}

      {advisory.gaps?.length ? (
        <Section title="What is missing">
          {advisory.gaps.map((gap, i) => (
            <Text key={i} style={styles.note}>• {gap}</Text>
          ))}
        </Section>
      ) : null}

      <FeedbackPrompt kind="advisory" fieldId={fieldId} />

      <View style={styles.footer}>
        <Button label="Update from satellite" onPress={requestRefresh} loading={busy} />
      </View>
    </ScrollView>
  );
};

const Section: React.FC<{
  title: string; right?: React.ReactNode; children: React.ReactNode;
}> = ({ title, right, children }) => (
  <View style={styles.section}>
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {right}
    </View>
    {children}
  </View>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  centered: { flex: 1, justifyContent: 'center', padding: spacing.lg },
  mapCard: {
    height: 200, borderRadius: radius.md, overflow: 'hidden', marginBottom: spacing.md,
  },
  summaryCard: {
    backgroundColor: colors.primary, borderRadius: radius.md, padding: spacing.md,
    marginBottom: spacing.md,
  },
  summary: { ...type.title, color: colors.onPrimary },
  section: {
    backgroundColor: colors.surface, borderRadius: radius.md,
    padding: spacing.md, marginBottom: spacing.sm,
  },
  sectionHeader: {
    flexDirection: 'row', justifyContent: 'space-between',
    alignItems: 'center', marginBottom: spacing.sm,
  },
  sectionTitle: { ...type.label, color: colors.textMuted, textTransform: 'uppercase' },
  headline: { ...type.title, color: colors.text, marginBottom: spacing.sm },
  body: { ...type.body, color: colors.text },
  note: { ...type.caption, color: colors.textMuted, marginTop: spacing.xs },
  warn: { ...type.body, color: colors.watch, marginTop: spacing.xs },
  good: { ...type.body, color: colors.ok, marginTop: spacing.xs },
  caption: { ...type.caption, color: colors.onPrimary, marginTop: spacing.xs, opacity: 0.9 },
  staleBanner: {
    ...type.caption, backgroundColor: '#FFF3CD', color: colors.text,
    padding: spacing.sm, borderRadius: radius.sm, marginBottom: spacing.md,
  },
  footer: { marginTop: spacing.md },
});

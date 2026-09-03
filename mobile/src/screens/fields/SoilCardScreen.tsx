/**
 * Soil Health Card entry, and the ribbon test for farmers without one.
 *
 * This screen is the answer to a verified upstream limitation: SoilGrids
 * returns null for the whole of India, so the global raster cannot supply soil
 * data for the pilot region. The government Soil Health Card can -- most Indian
 * farmers hold one, it is laboratory-measured, and it reports exactly the
 * available N/P/K and organic carbon the nutrient engine wants.
 *
 * The ribbon test is the fallback for anyone without a card. It is a standard
 * extension technique: wet a pinch of soil, work it between the fingers, and
 * describe what it does. It needs no equipment, no literacy and no signal, and
 * it yields the texture that drives the whole water balance.
 */

import React, { useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { Button } from '../../components/Button';
import { colors, radius, spacing, touch, type } from '../../constants/theme';
import { enqueue } from '../../db';
import { api, isOffline, readableError, setSoilCard } from '../../services/api';
import { useFieldStore } from '../../store/fieldSlice';

/** Maps to FEEL_TEST_MAP in backend/app/providers/soil.py. */
const FEEL_TESTS = [
  { key: 'gritty_no_ball', text: 'Gritty. Will not hold together in a ball.' },
  { key: 'gritty_weak_ball', text: 'Gritty. Makes a ball that falls apart easily.' },
  { key: 'gritty_ball_no_ribbon', text: 'Gritty. Holds a ball, but will not form a ribbon.' },
  { key: 'smooth_ball_short_ribbon', text: 'Smooth. Forms a short ribbon before breaking.' },
  { key: 'floury_short_ribbon', text: 'Floury or silky. Forms a short ribbon.' },
  { key: 'floury_no_grit', text: 'Very floury, no grit at all.' },
  { key: 'gritty_medium_ribbon', text: 'Gritty, but forms a medium ribbon.' },
  { key: 'medium_ribbon', text: 'Forms a medium ribbon, neither gritty nor floury.' },
  { key: 'smooth_medium_ribbon', text: 'Smooth. Forms a medium ribbon.' },
  { key: 'gritty_long_ribbon', text: 'Gritty, but forms a long ribbon.' },
  { key: 'smooth_long_ribbon', text: 'Smooth. Forms a long ribbon.' },
  { key: 'sticky_long_ribbon', text: 'Sticky. Forms a long ribbon easily.' },
];

const numberOrNull = (value: string): number | null => {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

export const SoilCardScreen: React.FC<{ route: any; navigation: any }> = ({
  route, navigation,
}) => {
  const { fieldId } = route.params;
  const refresh = useFieldStore((s) => s.refresh);

  const [mode, setMode] = useState<'card' | 'feel'>('card');
  const [ph, setPh] = useState('');
  const [oc, setOc] = useState('');
  const [n, setN] = useState('');
  const [p, setP] = useState('');
  const [k, setK] = useState('');
  const [feel, setFeel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const phValue = numberOrNull(ph);
  const ocValue = numberOrNull(oc);
  const phBad = phValue !== null && (phValue < 2 || phValue > 11);
  const ocBad = ocValue !== null && (ocValue < 0 || ocValue > 20);

  const anyCardValue = [ph, oc, n, p, k].some((v) => v.trim().length > 0);
  const canSave = mode === 'card' ? anyCardValue && !phBad && !ocBad : feel !== null;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (mode === 'card') {
        const body: Record<string, unknown> = {
          ph: phValue,
          organic_carbon_pct: ocValue,
          available_n_kg_ha: numberOrNull(n),
          available_p_kg_ha: numberOrNull(p),
          available_k_kg_ha: numberOrNull(k),
        };
        try {
          await setSoilCard(fieldId, body);
        } catch (err) {
          if (isOffline(err)) {
            enqueue('set_soil_card', { field_id: fieldId, body });
            Alert.alert('Saved on your phone', 'It will be sent when you are online.',
              [{ text: 'OK', onPress: () => navigation.goBack() }]);
            return;
          }
          throw err;
        }
      } else {
        await api.put(`/fields/${fieldId}/soil/feel-test`, { answer: feel });
      }
      await refresh();
      navigation.goBack();
    } catch (err) {
      setError(readableError(err, 'Could not save your soil information.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.tabs}>
        <Pressable
          onPress={() => setMode('card')}
          accessibilityRole="tab"
          accessibilityState={{ selected: mode === 'card' }}
          style={[styles.tab, mode === 'card' && styles.tabActive]}
        >
          <Text style={[styles.tabText, mode === 'card' && styles.tabTextActive]}>
            Soil Health Card
          </Text>
        </Pressable>
        <Pressable
          onPress={() => setMode('feel')}
          accessibilityRole="tab"
          accessibilityState={{ selected: mode === 'feel' }}
          style={[styles.tab, mode === 'feel' && styles.tabActive]}
        >
          <Text style={[styles.tabText, mode === 'feel' && styles.tabTextActive]}>
            I have no card
          </Text>
        </Pressable>
      </View>

      {mode === 'card' ? (
        <>
          <Text style={styles.hint}>
            Copy the numbers from your card. Leave anything blank that your card does
            not show — every value you add makes the fertiliser advice more accurate.
          </Text>

          <Field label="pH" value={ph} onChange={setPh} placeholder="7.5"
                 error={phBad ? 'pH is normally between 2 and 11.' : null} />
          <Field label="Organic carbon (%)" value={oc} onChange={setOc} placeholder="0.45"
                 error={ocBad ? 'Organic carbon is normally under 5%.' : null} />
          <Field label="Available nitrogen (kg/ha)" value={n} onChange={setN} placeholder="250" />
          <Field label="Available phosphorus (kg/ha)" value={p} onChange={setP} placeholder="15" />
          <Field label="Available potassium (kg/ha)" value={k} onChange={setK} placeholder="200" />

          <Text style={styles.note}>
            A laboratory card is the most trustworthy soil information available, and
            it is more reliable here than any satellite estimate.
          </Text>
        </>
      ) : (
        <>
          <Text style={styles.hint}>
            Take a pinch of soil, wet it slightly, and work it between your thumb and
            finger. Press it out into a flat ribbon. Which of these does it do?
          </Text>
          {FEEL_TESTS.map((option) => {
            const active = option.key === feel;
            return (
              <Pressable
                key={option.key}
                onPress={() => setFeel(option.key)}
                accessibilityRole="radio"
                accessibilityState={{ selected: active }}
                style={[styles.option, active && styles.optionActive]}
              >
                <Text style={[styles.optionText, active && styles.optionTextActive]}>
                  {option.text}
                </Text>
              </Pressable>
            );
          })}
          <Text style={styles.note}>
            This gives your soil texture, which decides how much water your field can
            hold. Fertiliser advice stays approximate until you enter a soil test.
          </Text>
        </>
      )}

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <View style={styles.footer}>
        <Button label="Save" onPress={save} loading={busy} disabled={!canSave} />
      </View>
    </ScrollView>
  );
};

const Field: React.FC<{
  label: string; value: string; onChange: (v: string) => void;
  placeholder: string; error?: string | null;
}> = ({ label, value, onChange, placeholder, error }) => (
  <View style={styles.fieldRow}>
    <Text style={styles.label}>{label}</Text>
    <TextInput
      style={[styles.input, error ? styles.inputError : null]}
      value={value}
      onChangeText={onChange}
      placeholder={placeholder}
      placeholderTextColor={colors.textMuted}
      keyboardType="decimal-pad"
      accessibilityLabel={label}
    />
    {error ? <Text style={styles.error}>{error}</Text> : null}
  </View>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  tabs: { flexDirection: 'row', marginBottom: spacing.md },
  tab: {
    flex: 1, minHeight: touch.minTarget, alignItems: 'center', justifyContent: 'center',
    borderBottomWidth: 3, borderBottomColor: colors.border,
  },
  tabActive: { borderBottomColor: colors.primary },
  tabText: { ...type.label, color: colors.textMuted },
  tabTextActive: { color: colors.primary },
  hint: { ...type.body, color: colors.textMuted, marginBottom: spacing.md },
  fieldRow: { marginBottom: spacing.md },
  label: { ...type.label, color: colors.text, marginBottom: spacing.xs },
  input: {
    ...type.body, color: colors.text, borderWidth: 2, borderColor: colors.border,
    borderRadius: radius.md, paddingHorizontal: spacing.md, minHeight: touch.minTarget,
  },
  inputError: { borderColor: colors.alert },
  option: {
    minHeight: touch.minTarget, justifyContent: 'center', padding: spacing.md,
    borderRadius: radius.md, borderWidth: 2, borderColor: colors.border,
    backgroundColor: colors.surface, marginBottom: spacing.sm,
  },
  optionActive: { backgroundColor: colors.primary, borderColor: colors.primaryDark },
  optionText: { ...type.body, color: colors.text },
  optionTextActive: { color: colors.onPrimary, fontWeight: '600' },
  note: { ...type.caption, color: colors.textMuted, marginTop: spacing.sm },
  error: { ...type.body, color: colors.alert, marginTop: spacing.xs },
  footer: { marginTop: spacing.lg },
});

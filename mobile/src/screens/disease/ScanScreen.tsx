/**
 * Leaf photo diagnosis.
 *
 * The bundled TFLite model runs first, on the phone, offline. Its softmax is
 * posted alongside the image and the SERVER re-applies the gate -- the client
 * check here only avoids a pointless upload on a confident answer; it is never
 * the authority. Duplicating the thresholds client-side and trusting them would
 * mean a stale APK could quietly bypass a tightened gate.
 */

import React, { useState } from 'react';
import { Alert, Image, ScrollView, StyleSheet, Text, View } from 'react-native';
import {
  launchCamera, launchImageLibrary, type PhotoQuality,
} from 'react-native-image-picker';

import { Button } from '../../components/Button';
import { FeedbackPrompt } from '../../components/FeedbackPrompt';
import { colors, radius, spacing, type } from '../../constants/theme';
import { enqueue, saveDiagnosis } from '../../db';
import { useDeviceTier } from '../../hooks/useDeviceTier';
import { classify } from '../../services/tflite';
import { isOffline, readableError, submitDiagnosis } from '../../services/api';
import type { Diagnosis, DiseasePrediction } from '../../types';

export const ScanScreen: React.FC<{ route: any }> = ({ route }) => {
  const cropCode: string = route.params?.cropCode ?? 'rice';
  const fieldId: string | null = route.params?.fieldId ?? null;
  const tier = useDeviceTier();

  const [imageUri, setImageUri] = useState<string | null>(null);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);

  const pick = async (fromCamera: boolean) => {
    const options = {
      mediaType: 'photo' as const,
      maxWidth: tier.captureMaxPx,
      maxHeight: tier.captureMaxPx,
      quality: tier.captureQuality as PhotoQuality,
      includeExtra: false,
    };
    const result = fromCamera
      ? await launchCamera(options)
      : await launchImageLibrary(options);
    const uri = result.assets?.[0]?.uri;
    if (uri) {
      setImageUri(uri);
      setDiagnosis(null);
      setQueued(false);
    }
  };

  const analyse = async () => {
    if (!imageUri) return;
    setBusy(true);
    try {
      // Always call classify: it loads the model lazily on first use and
      // returns an empty list if that fails. Guarding this with isModelLoaded()
      // was a deadlock -- the model only loads inside classify(), so the guard
      // was never true and the on-device path could never run at all.
      const predictions: DiseasePrediction[] = await classify(imageUri, cropCode);

      const result = await submitDiagnosis({
        cropCode, imageUri, fieldId, predictions,
      });
      saveDiagnosis(result, imageUri);
      setDiagnosis(result);
    } catch (error) {
      if (isOffline(error)) {
        enqueue('diagnosis', { crop_code: cropCode, field_id: fieldId }, imageUri);
        setQueued(true);
      } else {
        Alert.alert('Could not check the photo', readableError(error, 'Please try again.'));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.instructions}>
        Take a close photo of one affected leaf, in daylight, with the leaf filling
        most of the picture.
      </Text>

      {imageUri ? (
        <Image source={{ uri: imageUri }} style={styles.preview} resizeMode="cover" />
      ) : (
        <View style={[styles.preview, styles.placeholder]}>
          <Text style={styles.placeholderText}>No photo yet</Text>
        </View>
      )}

      <View style={styles.row}>
        <View style={styles.flex}>
          <Button label="Take photo" icon="📷" onPress={() => pick(true)} />
        </View>
        <View style={styles.gap} />
        <View style={styles.flex}>
          <Button label="Choose" variant="secondary" onPress={() => pick(false)} />
        </View>
      </View>

      {imageUri && !diagnosis && !queued ? (
        <View style={styles.action}>
          <Button label="Check this leaf" onPress={analyse} loading={busy} />
        </View>
      ) : null}

      {queued ? (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Saved on your phone</Text>
          <Text style={styles.body}>
            You have no internet. The photo will be checked automatically when you
            are back online.
          </Text>
        </View>
      ) : null}

      {diagnosis ? (
        <>
          <Result diagnosis={diagnosis} />
          {/* A wrong diagnosis is the most costly output here, and a farmer's
              correction is also the only ground-truth label this project can
              obtain from a real field. */}
          <FeedbackPrompt
            kind="diagnosis" diagnosisId={diagnosis.id} fieldId={fieldId}
          />
        </>
      ) : null}
    </ScrollView>
  );
};

const Result: React.FC<{ diagnosis: Diagnosis }> = ({ diagnosis }) => {
  const inconclusive = diagnosis.resolved_by === 'inconclusive';

  return (
    <View style={[styles.card, diagnosis.urgent && styles.urgentCard]}>
      <Text style={styles.cardTitle}>
        {inconclusive ? 'Not identified' : diagnosis.label}
      </Text>

      {!inconclusive ? (
        <Text style={styles.confidence}>
          {Math.round(diagnosis.confidence * 100)}% confident ·{' '}
          {diagnosis.resolved_by === 'on_device' ? 'checked on your phone' : 'checked online'}
        </Text>
      ) : null}

      {diagnosis.urgent ? (
        <Text style={styles.urgentText}>This spreads fast. Act today.</Text>
      ) : null}

      {diagnosis.ipm_actions.length ? (
        <>
          <Text style={styles.subhead}>What to do</Text>
          {diagnosis.ipm_actions.map((action, i) => (
            <Text key={i} style={styles.bullet}>• {action}</Text>
          ))}
        </>
      ) : null}

      {diagnosis.chemical_options.length ? (
        <>
          <Text style={styles.subhead}>Approved spray</Text>
          {diagnosis.chemical_options.map((option: any, i) => (
            <View key={i} style={styles.chemical}>
              <Text style={styles.body}>
                {option.active_ingredient} — {option.dose}
              </Text>
              <Text style={styles.warn}>{option.warning}</Text>
            </View>
          ))}
        </>
      ) : null}

      {diagnosis.notes.map((note, i) => (
        <Text key={i} style={styles.note}>{note}</Text>
      ))}

      {diagnosis.needs_expert_review ? (
        <Text style={styles.review}>
          Show this to your extension officer before spending money on treatment.
        </Text>
      ) : null}
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  instructions: { ...type.body, color: colors.textMuted, marginBottom: spacing.md },
  preview: { width: '100%', height: 260, borderRadius: radius.md, backgroundColor: colors.surface },
  placeholder: { alignItems: 'center', justifyContent: 'center' },
  placeholderText: { ...type.body, color: colors.textMuted },
  row: { flexDirection: 'row', marginTop: spacing.md },
  flex: { flex: 1 },
  gap: { width: spacing.sm },
  action: { marginTop: spacing.md },
  card: {
    backgroundColor: colors.surface, borderRadius: radius.md,
    padding: spacing.md, marginTop: spacing.md,
  },
  urgentCard: { borderLeftWidth: 6, borderLeftColor: colors.alert },
  cardTitle: { ...type.title, color: colors.text },
  confidence: { ...type.caption, color: colors.textMuted, marginTop: spacing.xs },
  urgentText: { ...type.label, color: colors.alert, marginTop: spacing.sm },
  subhead: {
    ...type.label, color: colors.textMuted, marginTop: spacing.md,
    textTransform: 'uppercase',
  },
  bullet: { ...type.body, color: colors.text, marginTop: spacing.xs },
  chemical: {
    marginTop: spacing.sm, padding: spacing.sm,
    backgroundColor: colors.bg, borderRadius: radius.sm,
  },
  body: { ...type.body, color: colors.text },
  warn: { ...type.caption, color: colors.watch, marginTop: spacing.xs },
  note: { ...type.caption, color: colors.textMuted, marginTop: spacing.sm },
  review: { ...type.body, color: colors.primary, marginTop: spacing.md, fontWeight: '600' },
});

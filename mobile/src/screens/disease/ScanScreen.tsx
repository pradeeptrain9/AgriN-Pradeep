/**
 * Leaf photo diagnosis.
 *
 * The bundled TFLite model runs first, on the phone, offline. Its softmax is
 * posted alongside the image and the SERVER re-applies the gate -- the client
 * check here only avoids a pointless upload on a confident answer; it is never
 * the authority. Duplicating the thresholds client-side and trusting them would
 * mean a stale APK could quietly bypass a tightened gate.
 */

import React, { useEffect, useState } from 'react';
import {
  Alert, Image, PermissionsAndroid, Platform, Pressable, ScrollView, StyleSheet,
  Text, View,
} from 'react-native';
import {
  launchCamera, launchImageLibrary, type PhotoQuality,
} from 'react-native-image-picker';

import { Button } from '../../components/Button';
import { FeedbackPrompt } from '../../components/FeedbackPrompt';
import { colors, radius, spacing, touch, type } from '../../constants/theme';
import { enqueue, loadCrops, saveCrops, saveDiagnosis } from '../../db';
import { useDeviceTier } from '../../hooks/useDeviceTier';
import { classify } from '../../services/tflite';
import { isOffline, listCrops, readableError, submitDiagnosis } from '../../services/api';
import type { CropOption, Diagnosis, DiseasePrediction } from '../../types';

export const ScanScreen: React.FC<{ route: any }> = ({ route }) => {
  const fieldId: string | null = route.params?.fieldId ?? null;
  const tier = useDeviceTier();

  // Arriving from a field, the crop is known. Arriving from the home screen --
  // a farmer with a leaf in their hand and no field mapped -- it has to be
  // asked for. Defaulting it would be worse than asking: the coverage gate is
  // scoped per crop, so checking a maize leaf as rice is exactly the case the
  // gate exists to refuse, and it would instead answer confidently.
  const [cropCode, setCropCode] = useState<string | null>(
    route.params?.cropCode ?? null,
  );
  // The mirror is the first source, so this works with no signal. But it is
  // only ever filled by the crop screen, which needs a field -- so a farmer who
  // has mapped nothing and just wants a leaf checked would find it empty and be
  // stuck. Fetch it here too when it is empty and there is a connection.
  const [crops, setCrops] = useState<CropOption[]>(
    () => loadCrops<CropOption>(),
  );
  const [cropsLoading, setCropsLoading] = useState(false);

  useEffect(() => {
    if (cropCode !== null) return;          // came from a field; crop is known
    let cancelled = false;
    const cached = loadCrops<CropOption>();
    if (cached.length === 0) setCropsLoading(true);

    listCrops()
      .then((list) => {
        saveCrops(list);
        if (!cancelled) setCrops(list as CropOption[]);
      })
      .catch(() => {
        // Offline with nothing mirrored is the one case with no way forward;
        // the empty state below says so plainly rather than showing a blank
        // list that looks broken.
      })
      .finally(() => {
        if (!cancelled) setCropsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [cropCode]);

  const [imageUri, setImageUri] = useState<string | null>(null);
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(false);

  /**
   * The app declares android.permission.CAMERA, and image-picker requires the
   * app to have obtained it before launchCamera -- it does not ask on your
   * behalf. Nothing requested it, so the camera button did nothing at all on a
   * fresh install.
   */
  const ensureCamera = async (): Promise<boolean> => {
    if (Platform.OS !== 'android') return true;
    const permission = PermissionsAndroid.PERMISSIONS.CAMERA;
    if (!permission) return true;
    if (await PermissionsAndroid.check(permission)) return true;

    const granted = await PermissionsAndroid.request(permission, {
      title: 'Use the camera',
      message: 'AgriN needs the camera to photograph the leaf you want checked.',
      buttonPositive: 'Allow',
      buttonNegative: 'Not now',
    });
    if (granted === PermissionsAndroid.RESULTS.GRANTED) return true;

    Alert.alert(
      'Camera not allowed',
      granted === PermissionsAndroid.RESULTS.NEVER_ASK_AGAIN
        ? 'Camera access was turned off for AgriN. Turn it on in Settings, or '
          + 'use Choose to pick a photo you have already taken.'
        : 'You can still use Choose to pick a photo you have already taken.',
    );
    return false;
  };

  const pick = async (fromCamera: boolean) => {
    if (fromCamera && !(await ensureCamera())) return;

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

    // Backing out is not a failure; say nothing.
    if (result.didCancel) return;

    // Everything else was silently discarded before, so a camera that could not
    // open was indistinguishable from a button that did nothing.
    if (result.errorCode) {
      Alert.alert(
        fromCamera ? 'Could not open the camera' : 'Could not open your photos',
        result.errorMessage
          || (result.errorCode === 'camera_unavailable'
            ? 'This device did not provide a camera.'
            : 'Please try again, or use the other option.'),
      );
      return;
    }

    const uri = result.assets?.[0]?.uri;
    if (!uri) {
      Alert.alert('No photo', 'Nothing came back from that. Please try again.');
      return;
    }

    setImageUri(uri);
    setDiagnosis(null);
    setQueued(false);
  };

  const analyse = async () => {
    if (!imageUri || !cropCode) return;
    setBusy(true);

    // Declared out here so the offline branch below can queue it. Scoped inside
    // the try, it was invisible to the catch and every queued photo was sent
    // with no predictions at all -- so the server gate saw nothing, could not
    // accept the on-device answer, and escalated to the paid cloud model. The
    // on-device model answers about 44% of photos for nothing, and offline
    // photos were the ones never getting that.
    let predictions: DiseasePrediction[] = [];

    try {
      // Always call classify: it loads the model lazily on first use and
      // returns an empty list if that fails. Guarding this with isModelLoaded()
      // was a deadlock -- the model only loads inside classify(), so the guard
      // was never true and the on-device path could never run at all.
      predictions = await classify(imageUri, cropCode);

      const result = await submitDiagnosis({
        cropCode, imageUri, fieldId, predictions,
      });
      saveDiagnosis(result, imageUri);
      setDiagnosis(result);
    } catch (error) {
      if (isOffline(error)) {
        enqueue(
          'diagnosis',
          { crop_code: cropCode, field_id: fieldId, predictions },
          imageUri,
        );
        setQueued(true);
      } else {
        Alert.alert('Could not check the photo', readableError(error, 'Please try again.'));
      }
    } finally {
      setBusy(false);
    }
  };

  if (!cropCode) {
    return (
      <ScrollView style={styles.container} contentContainerStyle={styles.content}>
        <Text style={styles.instructions}>
          Which crop is this leaf from?
        </Text>
        <Text style={styles.cropHint}>
          The check only knows the diseases of the crop you pick, and it will
          say so rather than guess if it does not recognise what it sees.
        </Text>

        {cropsLoading ? (
          <Text style={styles.cropHint}>Loading the crop list…</Text>
        ) : null}

        {!cropsLoading && crops.length === 0 ? (
          <Text style={styles.cropHint}>
            The crop list has not been downloaded yet and there is no internet
            right now. Connect once and it will be kept on this phone, so this
            works in the field afterwards.
          </Text>
        ) : null}

        {/* Only crops with a disease taxonomy. Offering the others guaranteed
            an inconclusive answer -- after the farmer had taken the photo and
            waited for it -- which reads as the app being broken rather than as
            the honest "this crop is not covered yet". */}
        {crops.filter((c) => c.diagnosable !== false).map((crop) => (
          <Pressable
            key={crop.code}
            onPress={() => setCropCode(crop.code)}
            accessibilityRole="button"
            accessibilityLabel={crop.label}
            style={styles.cropOption}
          >
            <Text style={styles.cropOptionText}>{crop.label}</Text>
          </Pressable>
        ))}

        {crops.some((c) => c.diagnosable === false) ? (
          <Text style={styles.cropHint}>
            {'\n'}
            Leaf checking is not available yet for{' '}
            {crops.filter((c) => c.diagnosable === false)
                  .map((c) => c.label).join(', ')}
            . Those crops have no disease list on this node, so a photo could
            only be guessed at. Ask your extension officer instead.
          </Text>
        ) : null}
      </ScrollView>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.instructions}>
        Take a close photo of one affected leaf, in daylight, with the leaf filling
        most of the picture.
      </Text>

      {/* Arrived without a field: show which crop is being checked, and let it
          be corrected without leaving the screen. */}
      {fieldId === null ? (
        <Pressable onPress={() => setCropCode(null)} style={styles.cropChip}>
          <Text style={styles.cropChipText}>
            Checking a {crops.find((c) => c.code === cropCode)?.label ?? cropCode}
            {' leaf \u00b7 change'}
          </Text>
        </Pressable>
      ) : null}

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
  cropHint: { ...type.body, color: colors.textMuted, marginBottom: spacing.md },
  cropOption: {
    paddingVertical: spacing.md,
    paddingHorizontal: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    marginBottom: spacing.sm,
    minHeight: touch.minTarget,
    justifyContent: 'center',
  },
  cropOptionText: { ...type.body, color: colors.text },
  cropChip: {
    alignSelf: 'flex-start',
    paddingVertical: spacing.xs,
    paddingHorizontal: spacing.sm,
    borderRadius: radius.sm,
    backgroundColor: colors.surface,
    marginBottom: spacing.md,
  },
  cropChipText: { ...type.label, color: colors.textMuted },
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

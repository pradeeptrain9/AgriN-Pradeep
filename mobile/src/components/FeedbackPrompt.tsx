/**
 * "Was this advice right?"
 *
 * The grievance path only exists if a farmer can reach it. An API nobody can
 * call is not recourse, so this sits directly under the advice it refers to
 * rather than behind a menu.
 *
 * Four verdicts, and the fourth is deliberately blunt. "It cost me crop" is the
 * report that matters most and the one people are least likely to volunteer, so
 * it is offered as plainly as the others rather than hidden behind "other".
 */

import React, { useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { enqueue } from '../db';
import { Button } from './Button';
import { colors, radius, spacing, touch, type } from '../constants/theme';
import { isOffline, readableError, submitFeedback, type Verdict } from '../services/api';

const CHOICES: Array<{ verdict: Verdict; label: string; icon: string }> = [
  { verdict: 'helpful', label: 'This was right', icon: '✓' },
  { verdict: 'unclear', label: 'I did not understand it', icon: '?' },
  { verdict: 'wrong', label: 'This was wrong', icon: '✕' },
  { verdict: 'harmful', label: 'It cost me crop or money', icon: '!' },
];

interface Props {
  kind: 'advisory' | 'diagnosis';
  fieldId?: string | null;
  diagnosisId?: string | null;
}

export const FeedbackPrompt: React.FC<Props> = ({ kind, fieldId, diagnosisId }) => {
  const [chosen, setChosen] = useState<Verdict | null>(null);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  // A one-tap "this was right" should not demand a written explanation; the
  // reports that need detail are the ones where something went wrong.
  const needsDetail = chosen === 'wrong' || chosen === 'harmful';

  const send = async (verdict: Verdict) => {
    setBusy(true);
    try {
      const result = await submitFeedback({
        kind, verdict, field_id: fieldId ?? null,
        diagnosis_id: diagnosisId ?? null,
        comment: comment.trim() || null,
      });
      setDone(result.message);
    } catch (error) {
      if (isOffline(error)) {
        // Never lose this. A farmer reporting that advice cost them a crop is
        // the single most important thing this app collects, and it is most
        // likely to be reported standing in the field, where there is no
        // signal. Queue it and say so plainly rather than promising a callback
        // that nothing has been set in motion to deliver.
        enqueue('feedback', {
          kind, verdict, field_id: fieldId ?? null,
          diagnosis_id: diagnosisId ?? null,
          comment: comment.trim() || null,
        });
        setDone(
          verdict === 'harmful'
            ? 'Saved on your phone. You have no internet right now. This will be sent as soon as you do, and then an extension officer should contact you.'
            : 'Saved on your phone. This will be sent when you are back online.',
        );
      } else {
        Alert.alert('Could not send', readableError(error, 'Please try again.'));
      }
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <View style={styles.card}>
        <Text style={styles.thanks}>{done}</Text>
      </View>
    );
  }

  return (
    <View style={styles.card}>
      <Text style={styles.title}>Was this right?</Text>
      <Text style={styles.hint}>
        Telling us when advice is wrong is how it gets better for everyone here.
      </Text>

      {CHOICES.map((choice) => {
        const active = chosen === choice.verdict;
        const severe = choice.verdict === 'harmful';
        return (
          <Pressable
            key={choice.verdict}
            onPress={() => {
              setChosen(choice.verdict);
              // Simple verdicts send immediately: an extra confirmation step is
              // friction on the feedback we most want.
              if (choice.verdict === 'helpful' || choice.verdict === 'unclear') {
                send(choice.verdict);
              }
            }}
            accessibilityRole="radio"
            accessibilityState={{ selected: active }}
            accessibilityLabel={choice.label}
            style={[
              styles.choice,
              active && styles.choiceActive,
              severe && styles.choiceSevere,
              active && severe && styles.choiceSevereActive,
            ]}
          >
            <Text style={[styles.icon, active && styles.iconActive]}>{choice.icon}</Text>
            <Text style={[styles.choiceText, active && styles.choiceTextActive]}>
              {choice.label}
            </Text>
          </Pressable>
        );
      })}

      {needsDetail ? (
        <View style={styles.detail}>
          <Text style={styles.hint}>
            {chosen === 'harmful'
              ? 'Tell us what happened. Someone will look at this and an extension '
                + 'officer should contact you.'
              : 'What actually happened in the field?'}
          </Text>
          <TextInput
            style={styles.input}
            value={comment}
            onChangeText={setComment}
            placeholder="In your own words"
            placeholderTextColor={colors.textMuted}
            multiline
            accessibilityLabel="What happened"
          />
          <Button
            label={chosen === 'harmful' ? 'Report this' : 'Send'}
            variant={chosen === 'harmful' ? 'danger' : 'primary'}
            onPress={() => send(chosen)}
            loading={busy}
          />
        </View>
      ) : null}
    </View>
  );
};

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface, borderRadius: radius.md,
    padding: spacing.md, marginTop: spacing.md,
  },
  title: { ...type.label, color: colors.text },
  hint: { ...type.caption, color: colors.textMuted, marginTop: spacing.xs,
          marginBottom: spacing.sm },
  choice: {
    flexDirection: 'row', alignItems: 'center', minHeight: touch.minTarget,
    borderWidth: 2, borderColor: colors.border, borderRadius: radius.md,
    paddingHorizontal: spacing.md, marginBottom: spacing.sm,
    backgroundColor: colors.bg,
  },
  choiceActive: { borderColor: colors.primary, backgroundColor: colors.primary },
  choiceSevere: { borderColor: colors.alert },
  choiceSevereActive: { backgroundColor: colors.alert, borderColor: colors.alert },
  icon: { ...type.title, color: colors.textMuted, width: 32 },
  iconActive: { color: colors.onPrimary },
  choiceText: { ...type.body, color: colors.text, flex: 1 },
  choiceTextActive: { color: colors.onPrimary, fontWeight: '600' },
  detail: { marginTop: spacing.sm },
  input: {
    ...type.body, color: colors.text, borderWidth: 2, borderColor: colors.border,
    borderRadius: radius.md, padding: spacing.md, minHeight: 96,
    textAlignVertical: 'top', marginBottom: spacing.sm,
  },
  thanks: { ...type.body, color: colors.primary },
});

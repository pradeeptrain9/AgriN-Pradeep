/**
 * What could be grown on this field, ranked and explained.
 *
 * This is the question a farmer has the moment they finish walking a boundary,
 * and until now the app answered it with an empty box and a list of fourteen
 * crop names in alphabetical order.
 *
 * Two rules this screen holds to:
 *
 *   * It ranks, it does not decide. A farmer knows their labour, their buyer
 *     and their land in ways no scoring function does, so choosing something
 *     off the list is a first-class action, not a fallback hidden at the
 *     bottom.
 *   * A warning is never quieter than the score it belongs to. A crop can top
 *     the ranking on nitrogen while the rainfall covers a tenth of its needs;
 *     if that reads as a recommendation, the screen has done harm.
 */

import React, { useCallback, useState } from 'react';
import { useFocusEffect } from '@react-navigation/native';
import {
  ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View,
} from 'react-native';

import { Button } from '../../components/Button';
import { colors, radius, spacing, touch, type } from '../../constants/theme';
import { getCropSuggestions, isOffline, readableError } from '../../services/api';
import type { CropSuggestion, CropSuggestions } from '../../types';

export const CropSuggestionScreen: React.FC<{ route: any; navigation: any }> = ({
  route, navigation,
}) => {
  const fieldId: string = route.params.fieldId;

  const [data, setData] = useState<CropSuggestions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getCropSuggestions(fieldId));
      setError(null);
    } catch (err) {
      setError(
        isOffline(err)
          ? 'No internet right now. Suggestions need the weather for your '
            + 'field, which is on the node. You can still choose a crop yourself.'
          : readableError(err, 'Could not load suggestions.'),
      );
    } finally {
      setLoading(false);
    }
  }, [fieldId]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const choose = (cropCode?: string) =>
    navigation.navigate('Crop', { fieldId, preselect: cropCode });

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.lede}>
        Based on the rain your field actually gets and what grew here last
        season. These are suggestions to weigh, not instructions.
      </Text>

      {loading && !data ? <ActivityIndicator color={colors.primary} /> : null}

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {data?.based_on ? (
        <View style={styles.basis}>
          <Text style={styles.basisText}>
            {data.based_on.weather_days > 0
              ? `${Math.round(data.based_on.rainfall_last_180d_mm)} mm of rain over the last `
                + `${data.based_on.weather_days} days.`
              : 'No weather recorded for this field yet.'}
            {data.based_on.previous_crop
              ? ` Last grown here: ${data.based_on.previous_crop}.`
              : ''}
          </Text>
        </View>
      ) : null}

      {data?.gaps?.map((gap, i) => (
        <Text key={i} style={styles.gap}>{gap}</Text>
      ))}

      {data?.suggestions.map((suggestion, index) => (
        <SuggestionCard
          key={suggestion.crop_code}
          suggestion={suggestion}
          rank={index + 1}
          onChoose={() => choose(suggestion.crop_code)}
        />
      ))}

      {/* Deliberately a full-width primary action, not a link. Growing
          something off the list is an ordinary thing for a farmer to do. */}
      <View style={styles.ownChoice}>
        <Text style={styles.ownChoiceText}>
          Growing something else? That is fine — the advice works the same for
          any crop on the list.
        </Text>
        <Button label="Choose a different crop" onPress={() => choose()} />
      </View>
    </ScrollView>
  );
};

const SuggestionCard: React.FC<{
  suggestion: CropSuggestion;
  rank: number;
  onChoose: () => void;
}> = ({ suggestion, rank, onChoose }) => (
  <Pressable
    style={styles.card}
    onPress={onChoose}
    accessibilityRole="button"
    accessibilityLabel={
      `${suggestion.label}, suggestion ${rank}. `
      + suggestion.reasons.join(' ')
      + (suggestion.warnings.length ? ` Warning: ${suggestion.warnings.join(' ')}` : '')
    }
  >
    <View style={styles.cardHeader}>
      <Text style={styles.cardTitle}>{suggestion.label}</Text>
      <Text style={styles.rank}>#{rank}</Text>
    </View>

    {suggestion.reasons.map((reason, i) => (
      <Text key={i} style={styles.reason}>• {reason}</Text>
    ))}

    {/* Before the water figure, not after: a farmer who stops reading here
        must still have seen the problem. */}
    {suggestion.warnings.map((warning, i) => (
      <View key={i} style={styles.warning}>
        <Text style={styles.warningText}>{warning}</Text>
      </View>
    ))}

    <Text style={styles.water}>
      Needs about {Math.round(suggestion.seasonal_water_need_mm)} mm of water
      over the season.
    </Text>
  </Pressable>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  lede: { ...type.body, color: colors.text, marginBottom: spacing.md },
  basis: {
    backgroundColor: colors.surface,
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginBottom: spacing.md,
  },
  basisText: { ...type.label, color: colors.textMuted },
  gap: { ...type.label, color: colors.textMuted, marginBottom: spacing.sm },
  error: { ...type.body, color: colors.alert, marginBottom: spacing.md },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    marginBottom: spacing.md,
    minHeight: touch.minTarget,
  },
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: spacing.xs,
  },
  cardTitle: { ...type.title, color: colors.text },
  rank: { ...type.label, color: colors.textMuted },
  reason: { ...type.body, color: colors.text, marginTop: 2 },
  warning: {
    backgroundColor: colors.bg,
    borderLeftWidth: 4,
    borderLeftColor: colors.watch,
    borderRadius: radius.sm,
    padding: spacing.sm,
    marginTop: spacing.sm,
  },
  warningText: { ...type.body, color: colors.text },
  water: { ...type.label, color: colors.textMuted, marginTop: spacing.sm },
  ownChoice: {
    marginTop: spacing.md,
    paddingTop: spacing.md,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  ownChoiceText: { ...type.body, color: colors.text, marginBottom: spacing.sm },
});

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { colors, radius, severityColor, severityIcon, type } from '../constants/theme';

const WORD: Record<string, string> = {
  ok: 'Healthy',
  watch: 'Watch',
  alert: 'Needs attention',
  unknown: 'Not known',
};

/**
 * Status is carried by an icon and a word as well as colour, so it survives
 * colour blindness and a phone screen washed out by direct sunlight.
 */
export const StatusPill: React.FC<{ severity?: string }> = ({ severity }) => (
  <View style={[styles.pill, { backgroundColor: severityColor(severity) }]}>
    <Text style={styles.icon}>{severityIcon(severity)}</Text>
    <Text style={styles.text}>{WORD[severity ?? 'unknown'] ?? 'Not known'}</Text>
  </View>
);

const styles = StyleSheet.create({
  pill: {
    flexDirection: 'row', alignItems: 'center', alignSelf: 'flex-start',
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: radius.lg,
  },
  icon: { color: colors.onPrimary, fontSize: 16, fontWeight: '700', marginRight: 8 },
  text: { ...type.label, color: colors.onPrimary },
});

import React from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, radius, touch, type } from '../constants/theme';

interface Props {
  label: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'danger';
  disabled?: boolean;
  loading?: boolean;
  icon?: string;
}

export const Button: React.FC<Props> = ({
  label, onPress, variant = 'primary', disabled, loading, icon,
}) => {
  const isPrimary = variant === 'primary';
  const isDanger = variant === 'danger';
  const background = disabled
    ? colors.border
    : isDanger ? colors.alert : isPrimary ? colors.primary : colors.surface;
  const foreground = disabled
    ? colors.textMuted
    : isPrimary || isDanger ? colors.onPrimary : colors.text;

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || loading}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => [
        styles.button,
        { backgroundColor: background, opacity: pressed ? 0.85 : 1 },
        variant === 'secondary' && styles.outlined,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={foreground} />
      ) : (
        <View style={styles.row}>
          {icon ? <Text style={[styles.icon, { color: foreground }]}>{icon}</Text> : null}
          <Text style={[type.label, styles.label, { color: foreground }]}>{label}</Text>
        </View>
      )}
    </Pressable>
  );
};

const styles = StyleSheet.create({
  button: {
    minHeight: touch.primaryButtonHeight,
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 20,
  },
  outlined: { borderWidth: 2, borderColor: colors.border },
  row: { flexDirection: 'row', alignItems: 'center' },
  icon: { fontSize: 22, marginRight: 10 },
  label: { fontSize: 18 },
});

import React, { useState } from 'react';
import { StyleSheet, Text, TextInput, View } from 'react-native';

import { Button } from '../../components/Button';
import { colors, spacing, touch, type } from '../../constants/theme';
import { readableError, verifyOtp } from '../../services/api';
import { useAuthStore } from '../../store/authSlice';

export const OtpScreen: React.FC<{ route: any; navigation: any }> = ({ route }) => {
  const { phone, devCode } = route.params ?? {};
  const [code, setCode] = useState(devCode ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setAuth = useAuthStore((s) => s.setAuth);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await verifyOtp(phone, code.trim());
      await setAuth(result.user_id, result.access_token);
    } catch (err) {
      setError(readableError(err, 'That code did not work.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.container}>
      <View>
        <Text style={styles.title}>Enter the code</Text>
        <Text style={styles.subtitle}>Sent to {phone}</Text>
        <TextInput
          style={styles.input}
          value={code}
          onChangeText={setCode}
          keyboardType="number-pad"
          maxLength={6}
          autoFocus
          accessibilityLabel="Six digit code"
        />
        {devCode ? <Text style={styles.hint}>Development code filled in for you.</Text> : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </View>
      <Button label="Continue" onPress={submit} disabled={code.length < 4} loading={busy} />
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1, backgroundColor: colors.bg, padding: spacing.lg, justifyContent: 'space-between',
  },
  title: { ...type.display, color: colors.text, marginTop: spacing.xl },
  subtitle: { ...type.body, color: colors.textMuted, marginTop: spacing.xs },
  input: {
    ...type.display, color: colors.text, letterSpacing: 10, textAlign: 'center',
    borderWidth: 2, borderColor: colors.border, borderRadius: 12,
    minHeight: touch.primaryButtonHeight, marginTop: spacing.lg,
  },
  hint: { ...type.caption, color: colors.textMuted, marginTop: spacing.sm },
  error: { ...type.body, color: colors.alert, marginTop: spacing.md },
});

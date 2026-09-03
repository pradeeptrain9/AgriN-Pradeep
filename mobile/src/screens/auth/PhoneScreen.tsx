import React, { useEffect, useState } from 'react';
import {
  KeyboardAvoidingView, Modal, Platform, Pressable, StyleSheet, Text, TextInput, View,
} from 'react-native';

import { Button } from '../../components/Button';
import { colors, radius, spacing, touch, type } from '../../constants/theme';
import { readableError, requestOtp } from '../../services/api';
import { currentNodeUrl, loadNodeUrl, nodeLabel, setNodeUrl } from '../../services/node';

export const PhoneScreen: React.FC<{ navigation: any }> = ({ navigation }) => {
  const [phone, setPhone] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [node, setNode] = useState(currentNodeUrl());
  const [nodeEditor, setNodeEditor] = useState(false);
  const [nodeDraft, setNodeDraft] = useState(currentNodeUrl());
  const [nodeError, setNodeError] = useState<string | null>(null);

  useEffect(() => {
    // AppNavigator already loaded this at startup; re-read so the field shows
    // the current value if it changed since.
    loadNodeUrl().then((url) => {
      setNode(url);
      setNodeDraft(url);
    });
  }, []);

  const saveNode = async () => {
    const result = await setNodeUrl(nodeDraft);
    if (!result.valid) {
      setNodeError(result.problem ?? 'That address cannot be used.');
      return;
    }
    setNode(result.normalised);
    setNodeError(null);
    setNodeEditor(false);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await requestOtp(phone.trim());
      navigation.navigate('Otp', { phone: phone.trim(), devCode: result.dev_code });
    } catch (err) {
      setError(readableError(err, 'Could not send the code. Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const valid = phone.replace(/\D/g, '').length >= 8;

  return (
    <>
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View>
        <Text style={styles.title}>AgriN</Text>
        <Text style={styles.subtitle}>Advice for your fields</Text>
      </View>

      <View>
        <Text style={styles.label}>Your mobile number</Text>
        <TextInput
          style={styles.input}
          value={phone}
          onChangeText={setPhone}
          placeholder="+91 98765 43210"
          placeholderTextColor={colors.textMuted}
          keyboardType="phone-pad"
          autoComplete="tel"
          maxLength={20}
          accessibilityLabel="Mobile number"
        />
        <Text style={styles.hint}>We send a 6-digit code. No password to remember.</Text>
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </View>

      <View>
        {/* Farmers should be able to see where their data is going, and a
            federated network means the answer is not always the same node. */}
        <Pressable
          onPress={() => setNodeEditor(true)}
          accessibilityRole="button"
          accessibilityLabel={`Connected to ${nodeLabel(node)}. Tap to change node.`}
          style={styles.nodeRow}
        >
          <Text style={styles.nodeText}>
            Connected to <Text style={styles.nodeName}>{nodeLabel(node)}</Text>
          </Text>
          <Text style={styles.nodeChange}>Change</Text>
        </Pressable>

        <Button label="Send code" onPress={submit} disabled={!valid} loading={busy} />
      </View>
    </KeyboardAvoidingView>

      {/* Sibling of KeyboardAvoidingView, not a child: nesting a Modal inside
          one stops it reopening after the first dismissal on Android. */}
      <Modal
        visible={nodeEditor}
        transparent
        animationType="fade"
        onRequestClose={() => setNodeEditor(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Your AgriN node</Text>
            <Text style={styles.hint}>
              Each country runs its own node. Your fields and photos stay on the
              one you choose here.
            </Text>
            <TextInput
              style={styles.nodeInput}
              value={nodeDraft}
              onChangeText={setNodeDraft}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              accessibilityLabel="Node address"
            />
            {nodeError ? (
              <Text style={[styles.error, styles.nodeErrorSpacing]}>{nodeError}</Text>
            ) : null}
            <Button label="Save" onPress={saveNode} />
            <View style={{ height: spacing.sm }} />
            <Button
              label="Cancel"
              variant="secondary"
              onPress={() => {
                setNodeDraft(node);
                setNodeError(null);
                setNodeEditor(false);
              }}
            />
          </View>
        </View>
      </Modal>
    </>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1, backgroundColor: colors.bg, padding: spacing.lg, justifyContent: 'space-between',
  },
  title: { ...type.display, color: colors.primary, marginTop: spacing.xl },
  subtitle: { ...type.body, color: colors.textMuted, marginTop: spacing.xs },
  label: { ...type.label, color: colors.text, marginBottom: spacing.sm },
  input: {
    ...type.title, color: colors.text, borderWidth: 2, borderColor: colors.border,
    borderRadius: 12, paddingHorizontal: spacing.md, minHeight: touch.minTarget,
  },
  hint: { ...type.caption, color: colors.textMuted, marginTop: spacing.sm },
  error: { ...type.body, color: colors.alert, marginTop: spacing.md },
  nodeRow: {
    flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center',
    paddingVertical: spacing.sm, marginBottom: spacing.sm,
  },
  nodeText: { ...type.caption, color: colors.textMuted },
  nodeName: { color: colors.text, fontWeight: '600' },
  nodeChange: { ...type.caption, color: colors.primary, fontWeight: '700' },
  modalBackdrop: {
    flex: 1, backgroundColor: 'rgba(0,0,0,0.45)',
    justifyContent: 'center', padding: spacing.lg,
  },
  modalCard: {
    backgroundColor: colors.bg, borderRadius: radius.md, padding: spacing.lg,
  },
  modalTitle: { ...type.title, color: colors.text, marginBottom: spacing.sm },
  nodeErrorSpacing: { marginTop: 0, marginBottom: spacing.md },
  nodeInput: {
    ...type.body, color: colors.text, borderWidth: 2, borderColor: colors.border,
    borderRadius: radius.md, paddingHorizontal: spacing.md, minHeight: touch.minTarget,
    marginTop: spacing.md, marginBottom: spacing.md,
  },
});

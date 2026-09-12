/**
 * Settings: which node holds your data, what is waiting to sync, and sign out.
 *
 * The node was previously only visible on the sign-in screen, which meant a
 * signed-in farmer could not see -- let alone change -- which country's node
 * holds their fields and photographs. In a federated network the node IS the
 * data-sovereignty boundary, so hiding it after sign-in is the wrong default.
 *
 * Sign-out deliberately does NOT clear the offline mirror. A farmer signing out
 * on a shared phone should not lose their mapped field boundaries, and the
 * mirror holds no credentials.
 */

import React, { useCallback, useState } from 'react';
import { Alert, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';

import { Button } from '../../components/Button';
import { NODE_URL_EDITABLE } from '../../constants/config';
import { colors, radius, spacing, touch, type } from '../../constants/theme';
import { loadFields, outboxCount } from '../../db';
import { currentNodeUrl, loadNodeUrl, nodeLabel, setNodeUrl } from '../../services/node';
import { drainOutbox } from '../../services/sync';
import { offlineStorageBytes } from '../../services/offlineTiles';
import { useAuthStore } from '../../store/authSlice';

export const SettingsScreen: React.FC<{ navigation: any }> = ({ navigation }) => {
  const { logout } = useAuthStore();

  const [node, setNode] = useState(currentNodeUrl());
  const [draft, setDraft] = useState(currentNodeUrl());
  const [editing, setEditing] = useState(false);
  const [nodeError, setNodeError] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
  const [fields, setFields] = useState(0);
  const [tiles, setTiles] = useState(0);
  const [syncing, setSyncing] = useState(false);

  useFocusEffect(
    useCallback(() => {
      loadNodeUrl().then((url) => {
        setNode(url);
        setDraft(url);
      });
      try {
        setPending(outboxCount());
        setFields(loadFields().length);
      } catch {
        // A fresh install has no tables yet; zero is the honest answer.
      }
      offlineStorageBytes().then(setTiles).catch(() => setTiles(0));
    }, []),
  );

  const saveNode = async () => {
    const result = await setNodeUrl(draft);
    if (!result.valid) {
      setNodeError(result.problem ?? 'That address cannot be used.');
      return;
    }
    setNode(result.normalised);
    setNodeError(null);
    setEditing(false);
    Alert.alert(
      'Node changed',
      'Sign out and back in for this to take effect. Your fields stay on this '
        + 'phone, but the new node will not know about them until they sync.',
    );
  };

  const sync = async () => {
    setSyncing(true);
    try {
      const result = await drainOutbox();
      setPending(result.remaining);
      Alert.alert(
        result.offline ? 'Still offline' : 'Sync finished',
        result.offline
          ? `${result.remaining} item(s) still waiting. They will go automatically `
            + 'when you have internet.'
          : `${result.sent} item(s) sent. ${result.remaining} still waiting.`,
      );
    } finally {
      setSyncing(false);
    }
  };

  const confirmSignOut = () => {
    const warning = pending > 0
      ? `${pending} item(s) have not reached the server yet. They stay on this `
        + 'phone and will be sent after you sign in again.\n\n'
      : '';
    Alert.alert(
      'Sign out?',
      `${warning}Your saved fields stay on this phone.`,
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Sign out', style: 'destructive', onPress: () => logout() },
      ],
    );
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.sectionTitle}>Your node</Text>
      <View style={styles.card}>
        <Text style={styles.value}>{nodeLabel(node)}</Text>
        <Text style={styles.hint}>
          Your fields, soil information and leaf photographs are held here. Each
          country runs its own node, and data is not moved between them.
        </Text>
        {!NODE_URL_EDITABLE ? null : editing ? (
          <>
            <TextInput
              style={styles.input}
              value={draft}
              onChangeText={setDraft}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              accessibilityLabel="Node address"
            />
            {nodeError ? <Text style={styles.error}>{nodeError}</Text> : null}
            <Button label="Save node" onPress={saveNode} />
            <View style={styles.gap} />
            <Button
              label="Cancel"
              variant="secondary"
              onPress={() => {
                setDraft(node);
                setNodeError(null);
                setEditing(false);
              }}
            />
          </>
        ) : (
          <Button label="Change node" variant="secondary" onPress={() => setEditing(true)} />
        )}
      </View>

      <Text style={styles.sectionTitle}>On this phone</Text>
      <View style={styles.card}>
        <Row label="Fields saved" value={String(fields)} />
        <Row label="Waiting to send" value={String(pending)} />
        <Row label="Map tiles stored" value={`${(tiles / 1048576).toFixed(1)} MB`} />
        {pending > 0 ? (
          <>
            <View style={styles.gap} />
            <Button label="Send now" onPress={sync} loading={syncing} />
          </>
        ) : null}
      </View>

      <Text style={styles.sectionTitle}>Account</Text>
      <View style={styles.card}>
        <Text style={styles.hint}>
          Signing out does not delete anything saved on this phone.
        </Text>
        <View style={styles.gap} />
        <Button label="Sign out" variant="danger" onPress={confirmSignOut} />
      </View>
    </ScrollView>
  );
};

const Row: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <View style={styles.row}>
    <Text style={styles.rowLabel}>{label}</Text>
    <Text style={styles.rowValue}>{value}</Text>
  </View>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingBottom: spacing.xl },
  sectionTitle: {
    ...type.label, color: colors.textMuted, textTransform: 'uppercase',
    marginTop: spacing.lg, marginBottom: spacing.sm,
  },
  card: { backgroundColor: colors.surface, borderRadius: radius.md, padding: spacing.md },
  value: { ...type.title, color: colors.text },
  hint: { ...type.caption, color: colors.textMuted, marginTop: spacing.xs, marginBottom: spacing.sm },
  input: {
    ...type.body, color: colors.text, borderWidth: 2, borderColor: colors.border,
    borderRadius: radius.md, paddingHorizontal: spacing.md, minHeight: touch.minTarget,
    marginBottom: spacing.sm,
  },
  row: {
    flexDirection: 'row', justifyContent: 'space-between',
    paddingVertical: spacing.sm,
  },
  rowLabel: { ...type.body, color: colors.textMuted },
  rowValue: { ...type.body, color: colors.text, fontWeight: '600' },
  error: { ...type.body, color: colors.alert, marginBottom: spacing.sm },
  gap: { height: spacing.sm },
});

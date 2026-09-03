/**
 * What happens to a farmer's data, before they hand any over.
 *
 * Shown once, after the first sign-in, and it has to be readable by someone who
 * reads slowly. So: short sentences, one idea each, and concrete nouns -- "your
 * field boundary", not "geospatial data".
 *
 * Two things it deliberately does NOT do:
 *
 *   - no pre-ticked box, and no way past this screen except an explicit tap
 *   - no burying the limits. A farmer is told the advice can be wrong and that
 *     they should check with an extension officer BEFORE they rely on it, not
 *     in a note under a diagnosis they have already acted on.
 *
 * The node address is named, because in a federated network that is the answer
 * to "where does my data live" and it is not the same for every user.
 */

import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { Button } from '../../components/Button';
import { colors, radius, spacing, touch, type } from '../../constants/theme';
import { currentNodeUrl, nodeLabel } from '../../services/node';
import { useAuthStore } from '../../store/authSlice';

const KEPT_HERE = [
  'The shape of your field, as you walked it',
  'What you are growing and when you sowed it',
  'Soil information you enter from your Soil Health Card',
  'Photographs of leaves you ask us to check',
  'Your mobile number, so you can sign in',
];

const LEAVES_THE_NODE = [
  'District-level summaries, combined with at least five other farms so no '
    + 'single farm can be picked out',
  'Nothing that identifies you, your phone number, or where your field is',
];

export const ConsentScreen: React.FC = () => {
  const [understood, setUnderstood] = useState(false);
  const acceptConsent = useAuthStore((s) => s.acceptConsent);
  const node = nodeLabel(currentNodeUrl());

  return (
    <View style={styles.container}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.title}>Before you start</Text>

        <Section title="What we keep">
          <Text style={styles.body}>
            This information stays on <Text style={styles.strong}>{node}</Text>.
          </Text>
          {KEPT_HERE.map((item) => (
            <Text key={item} style={styles.bullet}>• {item}</Text>
          ))}
        </Section>

        <Section title="What leaves this node">
          {LEAVES_THE_NODE.map((item) => (
            <Text key={item} style={styles.bullet}>• {item}</Text>
          ))}
          <Text style={styles.note}>
            Other countries' AgriN nodes can see those summaries. They cannot see
            your fields or your photographs.
          </Text>
        </Section>

        <Section title="What this advice is, and is not">
          <Text style={styles.bullet}>
            • It is a suggestion to check, not an instruction to follow.
          </Text>
          <Text style={styles.bullet}>
            • It can be wrong. Satellite pictures are often hidden by cloud, and
            the leaf checker is right about most photographs but not all of them.
          </Text>
          <Text style={styles.bullet}>
            • Ask your extension officer before spending money on treatment.
          </Text>
          <Text style={styles.bullet}>
            • If advice is wrong, or it costs you crop, tell us in the app. A
            person reads those reports.
          </Text>
        </Section>

        <Pressable
          onPress={() => setUnderstood((value) => !value)}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: understood }}
          accessibilityLabel="I have read and understood this"
          style={styles.checkRow}
        >
          <View style={[styles.box, understood && styles.boxChecked]}>
            {understood ? <Text style={styles.tick}>✓</Text> : null}
          </View>
          <Text style={styles.checkLabel}>
            I have read this and I understand it.
          </Text>
        </Pressable>
      </ScrollView>

      <View style={styles.footer}>
        <Button
          label="Continue"
          onPress={() => acceptConsent()}
          disabled={!understood}
        />
      </View>
    </View>
  );
};

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({
  title, children,
}) => (
  <View style={styles.section}>
    <Text style={styles.sectionTitle}>{title}</Text>
    {children}
  </View>
);

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  content: { padding: spacing.md, paddingBottom: spacing.lg },
  title: { ...type.display, color: colors.text, marginBottom: spacing.md },
  section: {
    backgroundColor: colors.surface, borderRadius: radius.md,
    padding: spacing.md, marginBottom: spacing.md,
  },
  sectionTitle: { ...type.title, color: colors.text, marginBottom: spacing.sm },
  body: { ...type.body, color: colors.text, marginBottom: spacing.xs },
  strong: { fontWeight: '700' },
  bullet: { ...type.body, color: colors.text, marginTop: spacing.xs },
  note: { ...type.caption, color: colors.textMuted, marginTop: spacing.sm },
  checkRow: {
    flexDirection: 'row', alignItems: 'center', minHeight: touch.minTarget,
    paddingVertical: spacing.sm,
  },
  box: {
    width: 32, height: 32, borderRadius: 6, borderWidth: 3,
    borderColor: colors.primary, marginRight: spacing.md,
    alignItems: 'center', justifyContent: 'center',
  },
  boxChecked: { backgroundColor: colors.primary },
  tick: { color: colors.onPrimary, fontSize: 20, fontWeight: '700' },
  checkLabel: { ...type.body, color: colors.text, flex: 1 },
  footer: {
    padding: spacing.md, borderTopWidth: 1, borderTopColor: colors.border,
  },
});

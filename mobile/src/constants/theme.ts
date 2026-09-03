/**
 * High-contrast, large-touch-target theme.
 *
 * Built for a cheap phone held in bright sunlight by someone who may not read
 * fluently: strong contrast, generous hit areas, meaning never carried by
 * colour alone (every status also has an icon and a word).
 */

export const colors = {
  bg: '#FFFFFF',
  surface: '#F4F6F4',
  border: '#D6DCD6',
  text: '#12200F',
  textMuted: '#4A5A47',
  primary: '#1B7F3B',
  primaryDark: '#125C2A',
  onPrimary: '#FFFFFF',

  // Status. Paired with icons and words in the UI, never colour alone.
  ok: '#1B7F3B',
  watch: '#B26A00',
  alert: '#B3261E',
  unknown: '#5A6B57',

  water: '#0B6E99',
} as const;

export const spacing = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 } as const;

export const radius = { sm: 6, md: 12, lg: 20 } as const;

/** 48dp is the Android accessibility minimum; 56 is comfortable with wet hands. */
export const touch = { minTarget: 56, primaryButtonHeight: 64 } as const;

export const type = {
  display: { fontSize: 30, fontWeight: '700' as const, lineHeight: 36 },
  title: { fontSize: 22, fontWeight: '700' as const, lineHeight: 28 },
  body: { fontSize: 18, fontWeight: '400' as const, lineHeight: 26 },
  label: { fontSize: 16, fontWeight: '600' as const, lineHeight: 22 },
  caption: { fontSize: 14, fontWeight: '400' as const, lineHeight: 20 },
} as const;

export const severityColor = (severity?: string): string => {
  switch (severity) {
    case 'ok': return colors.ok;
    case 'watch': return colors.watch;
    case 'alert': return colors.alert;
    default: return colors.unknown;
  }
};

/** Meaning must survive colour blindness and a washed-out screen. */
export const severityIcon = (severity?: string): string => {
  switch (severity) {
    case 'ok': return '✓';
    case 'watch': return '!';
    case 'alert': return '✕';
    default: return '?';
  }
};

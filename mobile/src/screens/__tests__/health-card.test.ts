/**
 * What the crop health card is allowed to claim.
 *
 * `severity` is 'unknown' in two unrelated situations: the satellite has never
 * had a clear view of the field, and it has but the view is more than twelve
 * days old, which downgrades an otherwise fine reading. The card keyed its
 * text off severity, so on a real field in monsoon Punjab it printed
 *
 *     No clear satellite picture of this field yet, so health cannot be scored.
 *     Last clear picture was 33 days ago.
 *
 * one line after the other. Both sentences cannot be true. The second one was,
 * and the first threw away a greenness measurement the node had computed and
 * sent.
 *
 * These read the screen source rather than rendering it: this project has no
 * component-test harness, and the property that matters is which field the
 * branch reads, which is exactly what regressed.
 */

const source = (): string => {
  const req = require as unknown as (m: string) => {
    readFileSync: (p: string, e: string) => string;
    join: (...p: string[]) => string;
    cwd: () => string;
  };
  const fs = req('fs');
  const path = req('path');
  return fs.readFileSync(
    path.join(req('process').cwd(), 'src', 'screens', 'fields', 'FieldDetailScreen.tsx'),
    'utf8',
  );
};

describe('the crop health card', () => {
  it('decides what to say from whether a picture exists', () => {
    expect(source()).toContain('health?.latest_ndvi == null ?');
  });

  it('does not decide it from severity', () => {
    // The regression itself. 'unknown' conflates "never seen" with "seen, but
    // a month ago", and only one of those means there is nothing to show.
    expect(source()).not.toContain("health?.severity === 'unknown' ?");
  });

  it('still shows severity in the pill, where it belongs', () => {
    // The grading is right; it was only the wrong thing to write prose from.
    expect(source()).toContain('<StatusPill severity={health?.severity} />');
  });

  it('says an old reading describes the crop then, not now', () => {
    // A month-old measurement is not nothing. It is simply not current, and
    // saying so is more useful than withholding it.
    expect(source()).toMatch(/describes the crop then, not now/);
  });

  it('warns that cloud can hide a problem that started since', () => {
    // Why staleness matters here specifically: the gap is not random, it is
    // cloud, and cloud comes with the weather that spreads disease.
    expect(source()).toMatch(/hide a problem/);
  });

  it('never claims there is no picture while also dating one', () => {
    // The contradiction, pinned. The "no picture yet" sentence must sit inside
    // the branch where latest_ndvi is null and nowhere else.
    // Anchored past the comment above the Section, which quotes the very
    // sentence being tested.
    const body = source().slice(source().indexOf('<Section title="Crop health"'));
    const start = body.indexOf('No clear satellite picture');
    const branch = body.lastIndexOf('latest_ndvi == null', start);
    expect(start).toBeGreaterThan(-1);
    expect(branch).toBeGreaterThan(-1);
    expect(start - branch).toBeLessThan(200);
  });
});

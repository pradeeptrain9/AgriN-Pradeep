import { parseNodeUrl, validateNodeUrl, nodeLabel } from '../node';

// This file exists because of a bug that shipped: the code used the global URL
// class, whose React Native polyfill throws on every getter. The https-only
// check never ran, and tapping Save crashed the settings screen. These tests
// pin the hand-rolled parser that replaced it.

describe('parseNodeUrl', () => {
  it('splits scheme, host, port and path', () => {
    expect(parseNodeUrl('https://node-in.agrin.org:8443/api')).toEqual({
      scheme: 'https',
      host: 'node-in.agrin.org',
      port: '8443',
      path: '/api',
    });
  });

  it('lowercases scheme and host but leaves the path alone', () => {
    const parsed = parseNodeUrl('HTTPS://Node-IN.Agrin.ORG/Api/V1');
    expect(parsed?.scheme).toBe('https');
    expect(parsed?.host).toBe('node-in.agrin.org');
    expect(parsed?.path).toBe('/Api/V1');
  });

  it('reports no port rather than inventing a default', () => {
    expect(parseNodeUrl('https://node-in.agrin.org')?.port).toBeNull();
  });

  it.each([
    ['no scheme', 'node-in.agrin.org'],
    ['scheme only', 'https://'],
    ['empty', ''],
    ['spaces only', '   '],
    ['a bare sentence', 'ask the officer'],
  ])('returns null for %s', (_label, raw) => {
    expect(parseNodeUrl(raw)).toBeNull();
  });

  it('does not throw on any input', () => {
    for (const raw of ['://', 'http://', 'https://:8080', ' ', 'https://a b']) {
      expect(() => parseNodeUrl(raw)).not.toThrow();
    }
  });
});

describe('validateNodeUrl', () => {
  it('accepts https', () => {
    expect(validateNodeUrl('https://node-in.agrin.org')).toEqual({
      valid: true,
      normalised: 'https://node-in.agrin.org',
    });
  });

  it('strips trailing slashes so the same node is not stored twice', () => {
    expect(validateNodeUrl('https://node-in.agrin.org///').normalised).toBe(
      'https://node-in.agrin.org',
    );
  });

  it.each(['http://localhost:8099', 'http://127.0.0.1:8099', 'http://10.0.2.2:8099'])(
    'allows cleartext to the loopback address %s used in development',
    (raw) => {
      expect(validateNodeUrl(raw).valid).toBe(true);
    },
  );

  it.each(['http://node-in.agrin.org', 'http://192.168.1.10:8099', 'http://agrin.org'])(
    'refuses cleartext to the real host %s',
    (raw) => {
      const result = validateNodeUrl(raw);
      expect(result.valid).toBe(false);
      expect(result.problem).toMatch(/https/i);
    },
  );

  it.each(['ftp://node-in.agrin.org', 'file:///etc/passwd', 'javascript://x'])(
    'refuses the non-http scheme %s',
    (raw) => {
      expect(validateNodeUrl(raw).valid).toBe(false);
    },
  );

  it('asks for an address rather than complaining about format when empty', () => {
    expect(validateNodeUrl('  ').problem).toBe('Enter a node address.');
  });

  it('never throws, whatever is typed', () => {
    for (const raw of ['://', 'https://', ' ', 'https://a b', '...']) {
      expect(() => validateNodeUrl(raw)).not.toThrow();
    }
  });
});

describe('nodeLabel', () => {
  it('shows the host so a farmer can see where their data goes', () => {
    expect(nodeLabel('https://node-in.agrin.org/api')).toContain('node-in.agrin.org');
  });

  it('falls back to the raw string rather than crashing on junk', () => {
    expect(() => nodeLabel('not a url')).not.toThrow();
  });
});

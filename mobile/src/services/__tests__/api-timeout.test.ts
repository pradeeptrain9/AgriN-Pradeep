/**
 * A sleeping node must not be reported as no signal.
 *
 * The India node runs on free hosting that stops the service after 15 minutes
 * idle and takes 30-60 seconds to answer the request that wakes it. An aborted
 * request carries no HTTP response, and `isOffline` is defined as "an axios
 * error with no response" -- so before this, every cold start told a farmer
 * holding a phone with four bars that they had no internet.
 *
 * That is the worst possible moment to lie to them. The first tap after a quiet
 * afternoon is when someone decides whether this app works at all, and "no
 * internet" is a message that makes them stop trying rather than tap again.
 */

import { isOffline, isTimeout, readableError } from '../api';

const timeoutError = (code = 'ECONNABORTED') => ({
  isAxiosError: true,
  code,
  message: `timeout of 60000ms exceeded`,
  response: undefined,
});

const noSignalError = () => ({
  isAxiosError: true,
  code: 'ERR_NETWORK',
  message: 'Network Error',
  response: undefined,
});

const serverError = (detail: string, status = 422) => ({
  isAxiosError: true,
  code: 'ERR_BAD_REQUEST',
  response: { status, data: { detail } },
});

describe('telling a timeout apart from no signal', () => {
  it.each(['ECONNABORTED', 'ETIMEDOUT'])('recognises %s as a timeout', (code) => {
    expect(isTimeout(timeoutError(code))).toBe(true);
  });

  it('does not call a genuine network failure a timeout', () => {
    expect(isTimeout(noSignalError())).toBe(false);
  });

  it('does not call a server rejection a timeout', () => {
    expect(isTimeout(serverError('Field not found', 404))).toBe(false);
  });

  it('does not throw on a non-axios error', () => {
    expect(() => isTimeout(new Error('boom'))).not.toThrow();
    expect(isTimeout(new Error('boom'))).toBe(false);
    expect(isTimeout(undefined)).toBe(false);
  });
});

describe('what the farmer is told', () => {
  it('says the node may be waking, not that there is no internet', () => {
    const message = readableError(timeoutError(), 'fallback');
    expect(message).toMatch(/waking up/i);
    expect(message).not.toMatch(/no internet/i);
  });

  it('invites another attempt, because the next one usually works', () => {
    // The request that timed out is the one that woke the server.
    expect(readableError(timeoutError(), 'fallback')).toMatch(/try again/i);
  });

  it('still says no internet when there genuinely is none', () => {
    expect(readableError(noSignalError(), 'fallback')).toMatch(/No internet/i);
  });

  it('still prefers the server’s own explanation when there is one', () => {
    // A 422 from the node explains something specific -- the field is too
    // small, the crop is unknown -- and that must not be flattened.
    expect(readableError(serverError('This field is 0.04 ha.'), 'fallback'))
      .toBe('This field is 0.04 ha.');
  });

  it('falls back when the server gives no detail', () => {
    expect(readableError({ isAxiosError: true, response: { status: 500, data: {} } },
      'Could not load.')).toBe('Could not load.');
  });
});

describe('a timeout still counts as offline for storage decisions', () => {
  it('keeps isOffline true so work is saved rather than lost', () => {
    // Deliberate. isOffline drives whether a walked boundary is written to the
    // outbox and whether the drain stops; a timeout is not proof the node
    // received anything, so the conservative branch is the safe one. Only the
    // *message* needed fixing, not where the work goes.
    expect(isOffline(timeoutError())).toBe(true);
  });

  it('still distinguishes a server rejection, which must not be queued', () => {
    // A 422 means the node understood and refused. Queueing that would replay
    // it for ever.
    expect(isOffline(serverError('Field is implausibly large'))).toBe(false);
  });
});

describe('the retry is bounded', () => {
  // Structural: the failure this guards against is an infinite retry loop
  // against a node that is down, which on a metered connection costs a farmer
  // money and drains the battery of the phone they need for the rest of the
  // day.
  const source = (): string => {
    const req = require as unknown as (m: string) => {
      readFileSync: (p: string, e: string) => string;
      join: (...p: string[]) => string;
      cwd: () => string;
    };
    const fs = req('fs');
    const path = req('path');
    return fs.readFileSync(
      path.join(req('process').cwd(), 'src', 'services', 'api.ts'), 'utf8',
    );
  };

  it('marks the request so it can only be retried once', () => {
    expect(source()).toContain('_retried');
  });

  it('retries only timeouts', () => {
    // Retrying a refused connection doubles the wait before a farmer is told
    // something true; retrying anything with a response replays a write the
    // server already accepted.
    expect(source()).toMatch(/!config\._retried && isTimeout\(error\)/);
  });

  it('allows longer than the host takes to wake', () => {
    // 30 s aborted before the node ever answered, which is what produced the
    // false "no internet" in the first place.
    const match = source().match(/REQUEST_TIMEOUT_MS = (\d+)/);
    expect(match).not.toBeNull();
    expect(Number(match![1])).toBeGreaterThanOrEqual(60000);
  });
});

describe('the release build points at the deployed node', () => {
  it('is the real host, not the placeholder', () => {
    const req = require as unknown as (m: string) => {
      readFileSync: (p: string, e: string) => string;
      join: (...p: string[]) => string;
      cwd: () => string;
    };
    const fs = req('fs');
    const path = req('path');
    const config = fs.readFileSync(
      path.join(req('process').cwd(), 'src', 'constants', 'config.ts'), 'utf8',
    );
    expect(config).not.toContain('agrin.example');
    expect(config).toMatch(/const PRODUCTION_URL = 'https:\/\//);
  });
});

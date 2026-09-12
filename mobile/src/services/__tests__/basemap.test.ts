/**
 * The map is an enhancement, not a dependency, and the attribution under it is
 * a licence condition. Both of those are properties of this module.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { DEFAULT_BASEMAP, cachedBasemap, resolveBasemap } from '../basemap';
import { getBasemap } from '../api';

jest.mock('../api', () => ({ getBasemap: jest.fn() }));

const KEY = 'agrin.basemap.v1';
const google = {
  provider: 'google-satellite',
  tile_url: 'https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}?session=abc&key=k',
  attribution: 'Imagery (c) Google',
  max_zoom: 20,
  satellite: true,
};

beforeEach(async () => {
  jest.clearAllMocks();
  await AsyncStorage.clear();
});

describe('resolveBasemap', () => {
  it('uses what the node serves', async () => {
    (getBasemap as jest.Mock).mockResolvedValue(google);
    const result = await resolveBasemap();
    expect(result.tile_url).toContain('googleapis.com');
    expect(result.attribution).toBe('Imagery (c) Google');
  });

  it('falls back to OpenStreetMap when the node cannot be reached', async () => {
    (getBasemap as jest.Mock).mockRejectedValue(new Error('offline'));
    const result = await resolveBasemap();
    expect(result).toEqual(DEFAULT_BASEMAP);
  });

  it('falls back to OpenStreetMap when signed out', async () => {
    // /basemap is authenticated because it hands over tiles the node pays for.
    // The map on a sign-in screen still has to draw something.
    (getBasemap as jest.Mock).mockRejectedValue({ response: { status: 401 } });
    await expect(resolveBasemap()).resolves.toEqual(DEFAULT_BASEMAP);
  });

  it('prefers a previous answer over the default when the node is down', async () => {
    (getBasemap as jest.Mock).mockResolvedValue(google);
    await resolveBasemap();
    (getBasemap as jest.Mock).mockRejectedValue(new Error('offline'));
    await AsyncStorage.setItem(
      KEY,
      JSON.stringify({ at: Date.now() - 40 * 3600 * 1000, basemap: google }),
    );
    const result = await resolveBasemap();
    expect(result.provider).toBe('google-satellite');
  });

  it('does not ask again while the cached answer is fresh', async () => {
    (getBasemap as jest.Mock).mockResolvedValue(google);
    await resolveBasemap();
    await resolveBasemap();
    expect(getBasemap).toHaveBeenCalledTimes(1);
  });

  it('asks again once the cached answer is older than a day', async () => {
    // The node's Map Tiles session expires. A stale token draws grey squares,
    // which looks exactly like no signal.
    (getBasemap as jest.Mock).mockResolvedValue(google);
    await AsyncStorage.setItem(
      KEY,
      JSON.stringify({ at: Date.now() - 25 * 3600 * 1000, basemap: google }),
    );
    await resolveBasemap();
    expect(getBasemap).toHaveBeenCalledTimes(1);
  });

  it('rejects a response with no tile template rather than drawing nothing', async () => {
    (getBasemap as jest.Mock).mockResolvedValue({ ...google, tile_url: 'https://x/' });
    await expect(resolveBasemap()).resolves.toEqual(DEFAULT_BASEMAP);
  });

  it('rejects a response with no attribution', async () => {
    // Showing someone else's tiles with no credit is a licence breach.
    (getBasemap as jest.Mock).mockResolvedValue({ ...google, attribution: '' });
    await expect(resolveBasemap()).resolves.toEqual(DEFAULT_BASEMAP);
  });

  it('survives corrupt stored data', async () => {
    await AsyncStorage.setItem(KEY, 'not json');
    (getBasemap as jest.Mock).mockRejectedValue(new Error('offline'));
    await expect(cachedBasemap()).resolves.toBeNull();
    await expect(resolveBasemap()).resolves.toEqual(DEFAULT_BASEMAP);
  });
});

describe('offline packs', () => {
  it('never store the node basemap', () => {
    // Google Maps Platform terms permit temporary caching only and forbid
    // storing tiles offline. An offline pack is that storage. The session
    // token in the URL would also expire inside the pack, leaving a farmer
    // with grey squares in the one place they cannot re-download.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const source: string = require('fs').readFileSync(
      require('path').resolve('src/services/offlineTiles.ts'),
      'utf8',
    );
    expect(source).toContain('OFFLINE_TILE_URL');
    expect(source).not.toContain('useBasemap');
    expect(source).not.toContain('resolveBasemap');
  });
});

/**
 * Which tiles to draw, decided by the node and cached on the phone.
 *
 * The node holds the Map Tiles key and pays for the session, so it chooses.
 * This module's only job is to make that choice survive a map opening before
 * the network answers -- and to make sure the attribution shown underneath is
 * the attribution for the tiles actually on screen, which is a licence
 * condition and not decoration.
 *
 * Three rules, in order of who they protect:
 *
 *   1. The map renders immediately from whatever is cached, and the fetch
 *      swaps it in afterwards. A farmer standing in a field opening the map
 *      never waits on a round trip to a sleeping node to see anything.
 *   2. A failed fetch is not an error. OpenStreetMap is the fallback and it
 *      works; the map is an enhancement, not a dependency.
 *   3. The cached entry expires well inside the node's own session lifetime.
 *      Google's session tokens expire, and a stale token draws grey squares --
 *      which looks exactly like no signal, in the one place a farmer is most
 *      likely to be standing without any.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { RASTER_TILE_URL, TILE_ATTRIBUTION, MAP_DEFAULTS } from '../constants/config';
import { getBasemap } from './api';
import type { Basemap } from '../types';

const KEY = 'agrin.basemap.v1';

/**
 * Keyless, unmetered, always available. Every failure path lands here, so it
 * has to be a basemap that works rather than a placeholder.
 */
export const DEFAULT_BASEMAP: Basemap = {
  provider: 'openstreetmap',
  tile_url: RASTER_TILE_URL,
  attribution: TILE_ATTRIBUTION,
  max_zoom: MAP_DEFAULTS.maxZoom,
  satellite: false,
};

/**
 * Shorter than the node's six-day session so the phone asks for a fresh one
 * before the old one dies, and long enough that a week of field work is not a
 * week of daily round trips.
 */
const CACHE_TTL_MS = 24 * 3600 * 1000;

interface Cached {
  at: number;
  basemap: Basemap;
}

const usable = (value: unknown): value is Basemap => {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<Basemap>;
  return (
    typeof candidate.tile_url === 'string' &&
    candidate.tile_url.includes('{z}') &&
    typeof candidate.attribution === 'string' &&
    candidate.attribution.length > 0
  );
};

/** The last basemap this phone was given, however old. Null if there is none. */
export const cachedBasemap = async (): Promise<Basemap | null> => {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Cached;
    return usable(parsed?.basemap) ? parsed.basemap : null;
  } catch {
    return null;
  }
};

const isFresh = async (): Promise<boolean> => {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return false;
    const parsed = JSON.parse(raw) as Cached;
    return Number.isFinite(parsed?.at) && Date.now() - parsed.at < CACHE_TTL_MS;
  } catch {
    return false;
  }
};

/**
 * Ask the node, unless the cached answer is still fresh.
 *
 * Returns the cached basemap, or the OpenStreetMap default, on any failure --
 * including the one that matters most, which is being signed out. /basemap is
 * authenticated because it hands over a tile URL the node pays for, so the map
 * on a sign-in screen has no token and must still draw something.
 */
export const resolveBasemap = async (): Promise<Basemap> => {
  const cached = await cachedBasemap();
  if (cached && (await isFresh())) return cached;

  try {
    const fresh = await getBasemap();
    if (!usable(fresh)) return cached ?? DEFAULT_BASEMAP;
    await AsyncStorage.setItem(
      KEY,
      JSON.stringify({ at: Date.now(), basemap: fresh } satisfies Cached),
    );
    return fresh;
  } catch {
    return cached ?? DEFAULT_BASEMAP;
  }
};

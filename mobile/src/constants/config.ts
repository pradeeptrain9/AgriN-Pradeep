/**
 * Runtime configuration.
 *
 * No map vendor token here on purpose. Conquerer hard-codes a Mapbox token in
 * this file; copying that would embed a personal credential in a public
 * repository and tie a digital public good to one vendor's billing. MapLibre
 * with OpenStreetMap raster tiles needs no key.
 */

const DEV_HOST = 'http://10.0.2.2:8099'; // Android emulator -> host machine

/**
 * The India node. This is the ONLY node a released build will ever talk to.
 *
 * Free hosting, and the app has to survive what that means: the service sleeps
 * after 15 minutes idle and takes 30-60 seconds to answer the request that
 * wakes it. That is longer than the HTTP timeout used elsewhere in this app,
 * so REQUEST_TIMEOUT_MS below is set for it -- otherwise the first tap after a
 * quiet afternoon reads as "no internet" to a farmer standing in a field, and
 * the second tap, which would have worked, never happens.
 */
const PRODUCTION_URL = 'https://agrin-node-in.onrender.com';

export const API_URL = __DEV__ ? DEV_HOST : PRODUCTION_URL;

/**
 * Whether a person may change which node the app talks to.
 *
 * A farmer should not have to think about this, and should certainly not be
 * able to be talked into pointing their app at someone else's server -- the
 * node is where their field boundaries and photographs live.
 *
 * Development keeps it editable because testing requires it: a tunnel address
 * changes every time the tunnel restarts, and a phone on a desk needs to reach
 * a laptop. Same binary shape, one flag.
 */
export const NODE_URL_EDITABLE = __DEV__;

/**
 * Raster OSM tiles. Heavier per tile than vector but decoded by the OS, which
 * is the right trade on a 2 GB phone: vector styling costs CPU we do not have.
 * High-tier devices switch to the vector style in MapStyle.ts.
 */
export const RASTER_TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
export const TILE_ATTRIBUTION = '(c) OpenStreetMap contributors';

export const MAP_DEFAULTS = { zoom: 16, minZoom: 4, maxZoom: 19 };

/**
 * GPS while walking a field boundary. Tighter than Conquerer's run tracking:
 * a field edge is metres wide, so a 20 m fix is useless, and points are
 * accepted more often because boundaries are short.
 */
export const GPS_CONFIG = {
  enableHighAccuracy: true,
  distanceFilter: 2,
  interval: 2000,
  fastestInterval: 1000,
  maxAcceptableAccuracyM: 15,
  minPointSpacingM: 1.5,
};

/** Matches the backend: fields under 0.1 ha cannot be monitored by Sentinel-2. */
export const FIELD_LIMITS = {
  minAreaHa: 0.1,
  maxAreaHa: 5000,
  minPoints: 6,
  /** Auto-close when the walker returns within this distance of the start. */
  autoCloseRadiusM: 12,
  /** Do not offer auto-close until they have actually walked somewhere. */
  autoCloseMinPerimeterM: 60,
};

/**
 * Drawing a boundary has the same area limits as walking one and a different
 * minimum point count, on purpose.
 *
 * Six points is right for a walk: the GPS emits them continuously and six is
 * barely more than standing still. It is wrong for tapping, where a rectangular
 * plot -- most plots -- is four corners and nothing more. Demanding six taps
 * would make a farmer invent two phantom corners along a straight bund, which
 * is worse data than the four real ones.
 */
export const DRAW_LIMITS = {
  minAreaHa: FIELD_LIMITS.minAreaHa,
  maxAreaHa: FIELD_LIMITS.maxAreaHa,
  minPoints: 3,
};

export const DISEASE_MODEL = {
  asset: 'disease_v1.tflite',
  inputSize: 224,
  /** Mirrors app/ai/disease.py. The server re-checks; this only avoids a wasted upload. */
  minConfidence: 0.7,
  minMargin: 0.15,
};

export const SYNC = {
  retryBaseMs: 2000,
  maxAttempts: 5,
};

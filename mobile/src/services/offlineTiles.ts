/**
 * Offline basemap packs.
 *
 * A farmer opens this app standing in a field, which is precisely where mobile
 * data is worst. Tiles are downloaded once, while the phone still has signal --
 * at home, at the dealer, on the road -- for a small box around each field.
 *
 * Packs are deliberately tiny. Zoom 13-17 over a couple of hectares is a few
 * hundred kilobytes; the same range over a district is hundreds of megabytes and
 * would both fill a cheap phone and violate the OpenStreetMap tile usage policy.
 * The bounding box is the field plus a small margin, never a region.
 *
 * ALWAYS OpenStreetMap, even when the node serves Google satellite imagery on
 * screen. Google Maps Platform terms permit only temporary caching of tiles and
 * forbid storing them for offline use; an offline pack is exactly the storage
 * they forbid, and the session token inside the URL would expire in the pack
 * anyway and leave a farmer with grey squares. The live map follows the node
 * (services/basemap.ts); what gets written to disk does not.
 */

import { OfflineManager } from '@maplibre/maplibre-react-native';

import { RASTER_TILE_URL, TILE_ATTRIBUTION } from '../constants/config';
import { rasterStyle } from '../constants/mapStyle';
import type { GeoJsonPolygon } from '../types';

/** Named so a future edit has to notice it is not the node's basemap. */
const OFFLINE_TILE_URL = RASTER_TILE_URL;
const OFFLINE_ATTRIBUTION = TILE_ATTRIBUTION;

const MIN_ZOOM = 13;
const MAX_ZOOM = 17;
/** Roughly 500 m of context around the field so the walk has landmarks. */
const MARGIN_DEGREES = 0.005;

export const packNameFor = (fieldId: string): string => `field-${fieldId}`;

export const boundsFor = (
  geometry: GeoJsonPolygon,
): { ne: [number, number]; sw: [number, number] } | null => {
  const ring = geometry.coordinates?.[0];
  if (!ring || ring.length === 0) return null;

  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const position of ring) {
    const lon = position[0]!;
    const lat = position[1]!;
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  return {
    sw: [minLon - MARGIN_DEGREES, minLat - MARGIN_DEGREES],
    ne: [maxLon + MARGIN_DEGREES, maxLat + MARGIN_DEGREES],
  };
};

export interface PackProgress {
  percentage: number;
  completedResourceCount: number;
  completedResourceSize: number;
}

/**
 * Download tiles for one field. Failure is non-fatal by design: the map is an
 * enhancement, and the boundary trace and every advisory work without it.
 */
export const downloadFieldTiles = async (
  fieldId: string,
  geometry: GeoJsonPolygon,
  onProgress?: (progress: PackProgress) => void,
): Promise<boolean> => {
  const bounds = boundsFor(geometry);
  if (!bounds) return false;

  const name = packNameFor(fieldId);
  try {
    const existing = await OfflineManager.getPack(name);
    if (existing) return true;

    await OfflineManager.createPack(
      {
        name,
        // Named constants, not the node's basemap, and not the bare default
        // either -- so removing this line has to be a decision. Licence note
        // above.
        styleURL: JSON.stringify(rasterStyle(OFFLINE_TILE_URL, OFFLINE_ATTRIBUTION)),
        bounds: [bounds.ne, bounds.sw],
        minZoom: MIN_ZOOM,
        maxZoom: MAX_ZOOM,
      },
      (_pack, status: any) => {
        onProgress?.({
          percentage: status?.percentage ?? 0,
          completedResourceCount: status?.completedResourceCount ?? 0,
          completedResourceSize: status?.completedResourceSize ?? 0,
        });
      },
      (_pack, error) => {
        console.warn('offline pack failed', error);
      },
    );
    return true;
  } catch (error) {
    console.warn('could not create offline pack', error);
    return false;
  }
};

export const removeFieldTiles = async (fieldId: string): Promise<void> => {
  try {
    await OfflineManager.deletePack(packNameFor(fieldId));
  } catch {
    // Nothing to delete is not an error.
  }
};

/** Total bytes held by all packs, so settings can show and clear it. */
export const offlineStorageBytes = async (): Promise<number> => {
  try {
    const packs = await OfflineManager.getPacks();
    return packs.reduce(
      (total: number, pack: any) => total + (pack?.status?.completedResourceSize ?? 0),
      0,
    );
  } catch {
    return 0;
  }
};

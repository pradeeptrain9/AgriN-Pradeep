/**
 * Basemap style, defined inline.
 *
 * No style URL and no API key. Every hosted vector-tile style worth using
 * (MapTiler, Stadia, Mapbox) needs an account and a key, which would put a
 * vendor credential in a digital public good and give one company a kill switch
 * over a farming network. An inline raster style needs neither.
 *
 * TILE POLICY, read before scaling: openstreetmap.org tiles are a donated
 * service with a usage policy that forbids bulk downloading and heavy automated
 * use. That is fine for a pilot of tens of farmers and NOT fine for a national
 * rollout. A production node serves its own basemap -- self-hosted raster tiles
 * or a PMTiles archive -- and points BASEMAP_TILE_URL at itself. The switch is
 * this one constant.
 */

import { RASTER_TILE_URL, TILE_ATTRIBUTION } from './config';

export const BASEMAP_TILE_URL = RASTER_TILE_URL;

/**
 * The background layer matters more than it looks: it renders before any tile
 * arrives and stays visible when none ever do. A farmer mapping a boundary with
 * no signal still sees their track drawn on a plain ground rather than a void.
 */
export const rasterStyle = (tileUrl: string = BASEMAP_TILE_URL) => ({
  version: 8,
  name: 'AgriN basemap',
  sources: {
    osm: {
      type: 'raster',
      tiles: [tileUrl],
      tileSize: 256,
      maxzoom: 19,
      attribution: TILE_ATTRIBUTION,
    },
  },
  layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#E8EDE6' } },
    { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': 1 } },
  ],
});

/** Satellite imagery would be better for field edges but every free source is keyed. */
export const SATELLITE_AVAILABLE = false;

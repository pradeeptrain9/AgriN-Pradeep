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
 * rollout.
 *
 * Which is why the tile URL is no longer a constant here. The node decides --
 * see services/basemap.ts -- and serves Google satellite imagery when it holds
 * a Map Tiles key, its own tiles when it is deployed at scale, and these
 * OpenStreetMap tiles when it holds neither. The values below are the fallback,
 * not the policy.
 */

import { RASTER_TILE_URL, TILE_ATTRIBUTION } from './config';

export const BASEMAP_TILE_URL = RASTER_TILE_URL;

/**
 * The background layer matters more than it looks: it renders before any tile
 * arrives and stays visible when none ever do. A farmer mapping a boundary with
 * no signal still sees their track drawn on a plain ground rather than a void.
 */
export const rasterStyle = (
  tileUrl: string = BASEMAP_TILE_URL,
  attribution: string = TILE_ATTRIBUTION,
  maxZoom: number = 19,
) => ({
  version: 8,
  name: 'AgriN basemap',
  sources: {
    // The source id stays 'osm' across providers. Renaming it on every switch
    // would give MapLibre a different style graph for the same map and throw
    // away the tiles already decoded on screen.
    osm: {
      type: 'raster',
      tiles: [tileUrl],
      tileSize: 256,
      maxzoom: maxZoom,
      // Whose tiles these are. A licence condition, not decoration, so it
      // travels with the URL rather than being fixed in the component.
      attribution,
    },
  },
  layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#E8EDE6' } },
    { id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-opacity': 1 } },
  ],
});

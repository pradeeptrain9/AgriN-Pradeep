// Verify the offline-pack bounds maths standalone (no MapLibre native module).
type GeoJsonPolygon = { type: 'Polygon'; coordinates: number[][][] };
const MARGIN_DEGREES = 0.005;

const boundsFor = (geometry: GeoJsonPolygon) => {
  const ring = geometry.coordinates?.[0];
  if (!ring || ring.length === 0) return null;
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const p of ring) {
    const lon = p[0]!, lat = p[1]!;
    if (lon < minLon) minLon = lon; if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat; if (lat > maxLat) maxLat = lat;
  }
  return { sw: [minLon - MARGIN_DEGREES, minLat - MARGIN_DEGREES] as [number, number],
           ne: [maxLon + MARGIN_DEGREES, maxLat + MARGIN_DEGREES] as [number, number] };
};

// Tile count for a bbox over a zoom range -- this is what lands on the phone.
const tileCount = (sw: number[], ne: number[], minZ: number, maxZ: number) => {
  const lon2x = (lon: number, z: number) => Math.floor(((lon + 180) / 360) * 2 ** z);
  const lat2y = (lat: number, z: number) => {
    const r = (lat * Math.PI) / 180;
    return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z);
  };
  let total = 0;
  for (let z = minZ; z <= maxZ; z++) {
    const x0 = lon2x(sw[0]!, z), x1 = lon2x(ne[0]!, z);
    const y0 = lat2y(ne[1]!, z), y1 = lat2y(sw[1]!, z);
    total += (Math.abs(x1 - x0) + 1) * (Math.abs(y1 - y0) + 1);
  }
  return total;
};

const field: GeoJsonPolygon = { type: 'Polygon', coordinates: [[
  [75.8500,30.9000],[75.8517,30.9000],[75.8517,30.9013],[75.8500,30.9013],[75.8500,30.9000]]] };

let pass = 0, fail = 0;
const check = (n: string, c: boolean, e = '') => {
  if (c) { pass++; console.log(`  ok   ${n}`); } else { fail++; console.log(`  FAIL ${n} ${e}`); }
};

const b = boundsFor(field)!;
check('bounds computed', b !== null);
check('sw is south-west of ne', b.sw[0] < b.ne[0] && b.sw[1] < b.ne[1]);
check('margin ~500 m applied', Math.abs((b.ne[1] - 30.9013) - 0.005) < 1e-9);

const tiles = tileCount(b.sw, b.ne, 13, 17);
const kb = tiles * 15; // ~15 KB per 256px OSM raster tile
console.log(`\n  z13-17 over one 2.3 ha field: ${tiles} tiles, ~${(kb/1024).toFixed(1)} MB`);
check('pack stays small enough for a cheap phone', kb / 1024 < 5, `${(kb/1024).toFixed(1)} MB`);

// A district-sized box is what the code must NOT do.
const district = tileCount([75.5, 30.6], [76.2, 31.2], 13, 17);
console.log(`  same range over a district: ${district} tiles, ~${(district*15/1024).toFixed(0)} MB`);
check('district pack is orders of magnitude larger', district > tiles * 50);

check('empty geometry returns null',
  boundsFor({ type: 'Polygon', coordinates: [[]] }) === null);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

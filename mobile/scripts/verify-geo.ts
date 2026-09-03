import {
  trackToPolygon, polygonAreaHa, ringSelfIntersects, validateBoundary,
  haversineDistance, pathLength, distanceToStart,
} from './src/utils/geo';
import type { Coordinate } from './src/types';

const LIMITS = { minAreaHa: 0.1, maxAreaHa: 5000, minPoints: 6 };
let pass = 0, fail = 0;
const check = (name: string, cond: boolean, extra = '') => {
  if (cond) { pass++; console.log(`  ok   ${name}`); }
  else { fail++; console.log(`  FAIL ${name} ${extra}`); }
};

const pt = (lon: number, lat: number, i = 0): Coordinate =>
  ({ latitude: lat, longitude: lon, timestamp: 1000 + i * 2000, accuracy: 5 });

// A real 2.34 ha rectangle near Ludhiana, walked with points every few metres.
const walkRectangle = (steps = 12): Coordinate[] => {
  const lon0 = 75.8500, lat0 = 30.9000, lon1 = 75.8517, lat1 = 30.9013;
  const out: Coordinate[] = []; let i = 0;
  for (let s = 0; s < steps; s++) out.push(pt(lon0 + (lon1-lon0)*s/steps, lat0, i++));
  for (let s = 0; s < steps; s++) out.push(pt(lon1, lat0 + (lat1-lat0)*s/steps, i++));
  for (let s = 0; s < steps; s++) out.push(pt(lon1 - (lon1-lon0)*s/steps, lat1, i++));
  for (let s = 0; s < steps; s++) out.push(pt(lon0, lat1 - (lat1-lat0)*s/steps, i++));
  return out;
};

console.log('\n== haversine ==');
check('Ludhiana rect width ~162 m',
  Math.abs(haversineDistance(30.9, 75.85, 30.9, 75.8517) - 162) < 5,
  `got ${haversineDistance(30.9,75.85,30.9,75.8517).toFixed(1)}`);
check('zero distance for same point', haversineDistance(30.9,75.85,30.9,75.85) === 0);

console.log('\n== track -> polygon ==');
const track = walkRectangle();
const poly = trackToPolygon(track);
check('produces a polygon', poly !== null);
const ring = poly!.coordinates[0]!;
check('ring is closed',
  ring[0]![0] === ring[ring.length-1]![0] && ring[0]![1] === ring[ring.length-1]![1]);
check('ring has >= 4 positions', ring.length >= 4, `len=${ring.length}`);
check('simplification thinned 48 pts', ring.length < track.length,
  `${track.length} -> ${ring.length}`);

// RFC 7946: exterior ring counter-clockwise. Shoelace > 0 == CCW.
const shoelace = (r: number[][]) => {
  let s = 0;
  for (let i = 0; i < r.length - 1; i++) s += r[i]![0]!*r[i+1]![1]! - r[i+1]![0]!*r[i]![1]!;
  return s / 2;
};
check('winding is counter-clockwise (RFC 7946)', shoelace(ring) > 0,
  `shoelace=${shoelace(ring).toExponential(2)}`);

console.log('\n== area ==');
const ha = polygonAreaHa(poly!);
check('area matches PostGIS 2.342 ha', Math.abs(ha - 2.342) < 0.05, `got ${ha.toFixed(3)}`);

console.log('\n== reversed walk (anticlockwise farmer) ==');
const revPoly = trackToPolygon([...walkRectangle()].reverse());
check('reversed walk still valid', revPoly !== null);
check('reversed walk normalised to CCW', shoelace(revPoly!.coordinates[0]!) > 0);
check('reversed walk same area',
  Math.abs(polygonAreaHa(revPoly!) - ha) < 0.01);

console.log('\n== self-intersection ==');
// Full-size bow-tie: same footprint as the good field, walked as a crossing
// loop. Its shoelace area is near zero even though the plot is 2+ ha, which is
// exactly why self-intersection must be detected before area.
const bowtie: Coordinate[] = [
  pt(75.8500,30.9000,0), pt(75.8508,30.9000,1), pt(75.8517,30.9013,2),
  pt(75.8517,30.9000,3), pt(75.8508,30.9006,4), pt(75.8500,30.9013,5),
  pt(75.8500,30.9006,6), pt(75.8500,30.9000,7),
];
const bowPoly = trackToPolygon(bowtie);
check('figure-of-eight detected', bowPoly !== null && ringSelfIntersects(bowPoly));
check('clean rectangle not flagged', !ringSelfIntersects(poly!));

console.log('\n== validation (mirrors backend rules) ==');
check('good field valid', validateBoundary(track, LIMITS).valid);
check('too few points rejected',
  validateBoundary(track.slice(0,3), LIMITS).problem === 'too_few_points');

// 0.04 ha plot -- the backend returned 422 for this exact case.
const tiny = (() => {
  const out: Coordinate[] = []; let i = 0;
  const lon0=75.85, lat0=30.90, lon1=75.8502, lat1=30.9002;
  for (let s=0;s<3;s++) out.push(pt(lon0+(lon1-lon0)*s/3, lat0, i++));
  for (let s=0;s<3;s++) out.push(pt(lon1, lat0+(lat1-lat0)*s/3, i++));
  for (let s=0;s<3;s++) out.push(pt(lon1-(lon1-lon0)*s/3, lat1, i++));
  for (let s=0;s<3;s++) out.push(pt(lon0, lat1-(lat1-lat0)*s/3, i++));
  return out;
})();
const tinyResult = validateBoundary(tiny, LIMITS);
check('0.04 ha plot rejected before upload', tinyResult.problem === 'too_small',
  `area=${tinyResult.areaHa.toFixed(3)} problem=${tinyResult.problem}`);
const bowResult = validateBoundary(bowtie, LIMITS);
check('bowtie rejected as crossing, not as too small',
  bowResult.problem === 'crosses_itself',
  `problem=${bowResult.problem} area=${bowResult.areaHa.toFixed(3)}`);

console.log('\n== walk metrics ==');
check('perimeter ~ 640 m', Math.abs(pathLength(track) - 640) < 40,
  `got ${pathLength(track).toFixed(0)}`);
check('distance back to start is large mid-walk',
  distanceToStart(track.slice(0, 24)) > 50);
check('distance back to start small when looped',
  distanceToStart([...track, pt(75.8500,30.9000,99)]) < 5);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

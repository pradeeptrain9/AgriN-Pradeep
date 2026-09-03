import area from '@turf/area';
import booleanClockwise from '@turf/boolean-clockwise';
import { polygon as turfPolygon, lineString } from '@turf/helpers';
import simplify from '@turf/simplify';

import type { Coordinate, GeoJsonPolygon } from '../types';

const EARTH_RADIUS_M = 6371000;

export const haversineDistance = (
  lat1: number, lon1: number, lat2: number, lon2: number,
): number => {
  const dLat = ((lat2 - lat1) * Math.PI) / 180;
  const dLon = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) *
      Math.cos((lat2 * Math.PI) / 180) *
      Math.sin(dLon / 2) ** 2;
  return EARTH_RADIUS_M * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
};

export const pathLength = (coords: Coordinate[]): number => {
  let total = 0;
  for (let i = 1; i < coords.length; i += 1) {
    const prev = coords[i - 1]!;
    const cur = coords[i]!;
    total += haversineDistance(prev.latitude, prev.longitude, cur.latitude, cur.longitude);
  }
  return total;
};

export const distanceToStart = (coords: Coordinate[]): number => {
  if (coords.length < 2) return 0;
  const first = coords[0]!;
  const last = coords[coords.length - 1]!;
  return haversineDistance(first.latitude, first.longitude, last.latitude, last.longitude);
};

/**
 * Close a walked track into a GeoJSON polygon.
 *
 * Three things the backend requires and a raw GPS track does not provide:
 * a closed ring (first position repeated at the end), counter-clockwise winding
 * per RFC 7946, and enough thinning that GPS jitter does not become hundreds of
 * meaningless vertices.
 */
export const trackToPolygon = (
  coords: Coordinate[],
  toleranceDegrees = 0.00002, // ~2 m
): GeoJsonPolygon | null => {
  if (coords.length < 3) return null;

  let ring: number[][] = coords.map((c) => [c.longitude, c.latitude]);

  const first = ring[0]!;
  const last = ring[ring.length - 1]!;
  if (first[0] !== last[0] || first[1] !== last[1]) {
    ring.push([first[0]!, first[1]!]);
  }
  if (ring.length < 4) return null;

  try {
    const simplified = simplify(lineString(ring), {
      tolerance: toleranceDegrees,
      highQuality: false,
    });
    const coordinates = simplified.geometry.coordinates as number[][];
    if (coordinates.length >= 4) ring = coordinates;
  } catch {
    // Simplification is an optimisation; the raw ring is still valid.
  }

  // Re-close in case simplification dropped the duplicated end position.
  const s = ring[0]!;
  const e = ring[ring.length - 1]!;
  if (s[0] !== e[0] || s[1] !== e[1]) ring.push([s[0]!, s[1]!]);
  if (ring.length < 4) return null;

  // RFC 7946 wants exterior rings counter-clockwise.
  if (booleanClockwise(lineString(ring))) ring.reverse();

  return { type: 'Polygon', coordinates: [ring] };
};

export const polygonAreaHa = (polygon: GeoJsonPolygon): number => {
  try {
    return area(turfPolygon(polygon.coordinates)) / 10000;
  } catch {
    return 0;
  }
};

/** Rough self-intersection check: a figure-of-eight walk is not a field. */
export const ringSelfIntersects = (polygon: GeoJsonPolygon): boolean => {
  const ring = polygon.coordinates[0];
  if (!ring || ring.length < 5) return false;

  const intersects = (p1: number[], p2: number[], p3: number[], p4: number[]): boolean => {
    const d = (p2[0]! - p1[0]!) * (p4[1]! - p3[1]!) - (p2[1]! - p1[1]!) * (p4[0]! - p3[0]!);
    if (Math.abs(d) < 1e-12) return false;
    const u = ((p3[0]! - p1[0]!) * (p4[1]! - p3[1]!) - (p3[1]! - p1[1]!) * (p4[0]! - p3[0]!)) / d;
    const v = ((p3[0]! - p1[0]!) * (p2[1]! - p1[1]!) - (p3[1]! - p1[1]!) * (p2[0]! - p1[0]!)) / d;
    return u > 0 && u < 1 && v > 0 && v < 1;
  };

  for (let i = 0; i < ring.length - 1; i += 1) {
    for (let j = i + 2; j < ring.length - 1; j += 1) {
      // Skip the pair that shares the closing vertex.
      if (i === 0 && j === ring.length - 2) continue;
      if (intersects(ring[i]!, ring[i + 1]!, ring[j]!, ring[j + 1]!)) return true;
    }
  }
  return false;
};

export interface BoundaryValidation {
  valid: boolean;
  areaHa: number;
  problem?: string;
}

/** Mirrors the backend's rejection rules so the failure happens before upload. */
export const validateBoundary = (
  coords: Coordinate[],
  limits: { minAreaHa: number; maxAreaHa: number; minPoints: number },
): BoundaryValidation => {
  if (coords.length < limits.minPoints) {
    return { valid: false, areaHa: 0, problem: 'too_few_points' };
  }
  const polygon = trackToPolygon(coords);
  if (!polygon) return { valid: false, areaHa: 0, problem: 'not_a_shape' };

  // Self-intersection is checked BEFORE area on purpose: the shoelace area of a
  // self-crossing ring is not meaningful (opposing lobes cancel), so a
  // figure-of-eight walk reports a tiny area and the farmer would be told to
  // walk a bigger field when the real problem is that they crossed their path.
  if (ringSelfIntersects(polygon)) {
    return { valid: false, areaHa: 0, problem: 'crosses_itself' };
  }

  const areaHa = polygonAreaHa(polygon);
  if (areaHa < limits.minAreaHa) return { valid: false, areaHa, problem: 'too_small' };
  if (areaHa > limits.maxAreaHa) return { valid: false, areaHa, problem: 'too_large' };

  return { valid: true, areaHa };
};

/**
 * Area-weighted centroid of the outer ring, which is what PostGIS ST_Centroid
 * returns for a polygon. Used only for a field mapped offline, so that the
 * phone's own copy has the same centre the node will compute for it later.
 *
 * The vertex mean is not the same thing and is wrong for any field walked with
 * unevenly spaced GPS points, which is every real one.
 */
export const polygonCentroid = (polygon: GeoJsonPolygon): [number, number] => {
  type Position = [number, number];
  const ring: Position[] = (polygon.coordinates[0] ?? []).map(
    (p) => [p[0] ?? 0, p[1] ?? 0],
  );

  // Closed rings repeat the first point; the shoelace terms handle the wrap.
  const first = ring[0];
  const last = ring[ring.length - 1];
  const points =
    ring.length > 1 && first && last && first[0] === last[0] && first[1] === last[1]
      ? ring.slice(0, -1)
      : ring;

  const mean = (): [number, number] => [
    points.reduce((sum, p) => sum + p[0], 0) / points.length,
    points.reduce((sum, p) => sum + p[1], 0) / points.length,
  ];

  if (points.length === 0) return [0, 0];
  if (points.length < 3) return mean();

  // Work relative to the first vertex. A field is a fraction of a degree across
  // while its longitude is ~77, so the shoelace cross products are ~1e3 with
  // differences ~1e-3: computed on the raw values, six digits of precision are
  // lost to cancellation and the centroid lands a quarter of a metre out.
  // Shifting the origin is exact for the centroid, which is translation
  // equivariant, and keeps every intermediate near zero.
  const [ox, oy] = points[0] as Position;

  let twiceArea = 0;
  let x = 0;
  let y = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [px0, py0] = points[i] as Position;
    const [px1, py1] = points[(i + 1) % points.length] as Position;
    const x0 = px0 - ox;
    const y0 = py0 - oy;
    const x1 = px1 - ox;
    const y1 = py1 - oy;
    const cross = x0 * y1 - x1 * y0;
    twiceArea += cross;
    x += (x0 + x1) * cross;
    y += (y0 + y1) * cross;
  }

  // A degenerate ring (all points collinear) has zero area; fall back to the
  // vertex mean rather than dividing by zero.
  if (twiceArea === 0) return mean();

  return [ox + x / (3 * twiceArea), oy + y / (3 * twiceArea)];
};

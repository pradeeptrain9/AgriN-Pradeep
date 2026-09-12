/**
 * Boundaries drawn by tapping corners, rather than walked.
 *
 * The geometry is shared with the walk path and already tested; what is new,
 * and what is tested here, is that the rules bend in the two places tapping
 * differs from walking and nowhere else:
 *
 *   * the minimum point count, because a rectangular plot is four corners and
 *     demanding six taps makes a farmer invent two;
 *   * simplification, because a tapped corner was chosen and a GPS sample was
 *     not.
 *
 * Everything that protects against a nonsense field -- self-intersection, the
 * 0.1 ha satellite floor, the upper bound -- must still bite exactly as hard.
 */

import { DRAW_LIMITS, FIELD_LIMITS } from '../../constants/config';
import { polygonAreaHa, trackToPolygon, validateBoundary } from '../geo';
import type { Coordinate } from '../../types';

const at = (longitude: number, latitude: number): Coordinate => ({
  longitude, latitude, timestamp: 0,
});

/**
 * A square of roughly `side` metres, near Bengaluru.
 *
 * The longitude step is divided by cos(latitude) on purpose. A degree of
 * longitude at 13 N is about 108.5 km, not 111.3, so using the same delta for
 * both axes builds a rectangle 5% narrow -- which reads as a 3% area error in
 * the assertions below and looks like a bug in the geometry rather than in the
 * test's own arithmetic.
 */
const square = (side: number): Coordinate[] => {
  const lat = 13.0;
  const dLat = side / 111_320;
  const dLon = dLat / Math.cos((lat * Math.PI) / 180);
  return [
    at(77.0, lat), at(77.0 + dLon, lat),
    at(77.0 + dLon, lat + dLat), at(77.0, lat + dLat),
  ];
};

const NO_SIMPLIFICATION = 0;

describe('how many corners a drawn field needs', () => {
  it('accepts four, because most plots are rectangles', () => {
    // The walk path requires six. A farmer tapping a rectangular plot would
    // have to place two corners that are not corners to satisfy that, which is
    // worse data than the four real ones.
    expect(validateBoundary(square(120), DRAW_LIMITS, NO_SIMPLIFICATION).valid).toBe(true);
  });

  it('accepts three, the fewest that enclose an area', () => {
    const triangle = [at(77.0, 13.0), at(77.002, 13.0), at(77.001, 13.002)];
    expect(validateBoundary(triangle, DRAW_LIMITS, NO_SIMPLIFICATION).valid).toBe(true);
  });

  it.each([0, 1, 2])('refuses %i corner(s)', (count) => {
    const partial = square(120).slice(0, count);
    const result = validateBoundary(partial, DRAW_LIMITS, NO_SIMPLIFICATION);
    expect(result.valid).toBe(false);
    expect(result.problem).toBe('too_few_points');
  });

  it('is a lower bar than walking, and only that', () => {
    expect(DRAW_LIMITS.minPoints).toBeLessThan(FIELD_LIMITS.minPoints);
    expect(DRAW_LIMITS.minAreaHa).toBe(FIELD_LIMITS.minAreaHa);
    expect(DRAW_LIMITS.maxAreaHa).toBe(FIELD_LIMITS.maxAreaHa);
  });
});

describe('every corner the farmer tapped is kept', () => {
  it('does not thin a deliberate vertex away', () => {
    // A bund with a slight dog-leg: the middle point sits about a metre off the
    // straight line, which is exactly what the walk path's 2 m tolerance exists
    // to discard as GPS jitter. Here it is a corner someone aimed at.
    const kinked: Coordinate[] = [
      at(77.0, 13.0),
      at(77.0005, 13.000009), // ~1 m off the chord
      at(77.001, 13.0),
      at(77.001, 13.001),
      at(77.0, 13.001),
    ];

    const drawn = trackToPolygon(kinked, NO_SIMPLIFICATION);
    const walked = trackToPolygon(kinked);

    // 5 corners + the repeated closing position.
    expect(drawn!.coordinates[0]!.length).toBe(6);
    expect(walked!.coordinates[0]!.length).toBeLessThan(6);
  });

  it('still closes the ring and winds it the way GeoJSON wants', () => {
    // Clockwise input: RFC 7946 wants an exterior ring counter-clockwise, and
    // PostGIS is handed this verbatim.
    const clockwise = [at(77.0, 13.0), at(77.0, 13.001), at(77.001, 13.001), at(77.001, 13.0)];
    const ring = trackToPolygon(clockwise, NO_SIMPLIFICATION)!.coordinates[0]!;

    expect(ring[0]).toEqual(ring[ring.length - 1]);

    let twiceArea = 0;
    for (let i = 0; i < ring.length - 1; i += 1) {
      twiceArea += ring[i]![0]! * ring[i + 1]![1]! - ring[i + 1]![0]! * ring[i]![1]!;
    }
    expect(twiceArea).toBeGreaterThan(0); // counter-clockwise
  });
});

describe('the checks that must not soften', () => {
  it('refuses a plot below the satellite floor', () => {
    // 20 m square is 0.04 ha. Sentinel-2 pixels are 10 m, so after the boundary
    // erosion there is nothing left to sample -- the same reason the walk path
    // refuses it, and drawing does not make the satellite see better.
    const result = validateBoundary(square(20), DRAW_LIMITS, NO_SIMPLIFICATION);
    expect(result.valid).toBe(false);
    expect(result.problem).toBe('too_small');
  });

  it('refuses a bow-tie, whichever corner order produced it', () => {
    const bowtie = [at(77.0, 13.0), at(77.002, 13.002), at(77.002, 13.0), at(77.0, 13.002)];
    const result = validateBoundary(bowtie, DRAW_LIMITS, NO_SIMPLIFICATION);
    expect(result.valid).toBe(false);
    expect(result.problem).toBe('crosses_itself');
  });

  it('reports crossing rather than a tiny area for a bow-tie', () => {
    // The shoelace area of a self-crossing ring cancels between the lobes, so
    // checking area first would tell a farmer their field is too small when
    // the real problem is the order they tapped in.
    const bowtie = [at(77.0, 13.0), at(77.002, 13.002), at(77.002, 13.0), at(77.0, 13.002)];
    expect(polygonAreaHa(trackToPolygon(bowtie, NO_SIMPLIFICATION)!)).toBeLessThan(1);
    expect(validateBoundary(bowtie, DRAW_LIMITS, NO_SIMPLIFICATION).problem)
      .toBe('crosses_itself');
  });

  it('refuses an implausibly large block', () => {
    const result = validateBoundary(square(300_000), DRAW_LIMITS, NO_SIMPLIFICATION);
    expect(result.valid).toBe(false);
    expect(result.problem).toBe('too_large');
  });
});

describe('the area a drawn field reports', () => {
  it('matches the square that was tapped', () => {
    // Every fertiliser and water figure in the advisory is per hectare, so this
    // number multiplies into all of them. A 200 m square is 4 ha.
    const result = validateBoundary(square(200), DRAW_LIMITS, NO_SIMPLIFICATION);
    expect(result.areaHa).toBeGreaterThan(3.9);
    expect(result.areaHa).toBeLessThan(4.1);
  });

  it('is the same number a walk of the same corners would give', () => {
    // Drawing changes the provenance of a boundary, never the arithmetic on it.
    const corners = square(200);
    const drawn = validateBoundary(corners, DRAW_LIMITS, NO_SIMPLIFICATION).areaHa;
    const walked = polygonAreaHa(trackToPolygon(corners)!);
    expect(drawn).toBeCloseTo(walked, 6);
  });
});

describe('the walk path is untouched', () => {
  it('still demands six points when the walk limits are used', () => {
    // Regression guard: the tolerance parameter added for drawing defaults to
    // the walk's own, so passing nothing must behave exactly as before.
    expect(validateBoundary(square(120), FIELD_LIMITS).problem).toBe('too_few_points');
  });

  it('still thins a GPS track when no tolerance is given', () => {
    const jittery: Coordinate[] = [];
    for (let i = 0; i <= 40; i += 1) {
      jittery.push(at(77.0 + i * 0.00002, 13.0 + (i % 2) * 0.000005));
    }
    jittery.push(at(77.0008, 13.0008), at(77.0, 13.0008));
    expect(trackToPolygon(jittery)!.coordinates[0]!.length)
      .toBeLessThan(jittery.length);
  });
});

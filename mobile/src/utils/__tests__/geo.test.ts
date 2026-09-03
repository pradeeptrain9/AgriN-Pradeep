import { polygonCentroid, polygonAreaHa } from '../geo';

// A field mapped with no signal computes its own centroid, because the node
// that would normally do it is unreachable. If this disagrees with PostGIS the
// phone's copy of the field sits somewhere the node does not think it is.

const square = {
  type: 'Polygon' as const,
  coordinates: [[
    [77.594, 12.971], [77.5952, 12.971], [77.5952, 12.9722], [77.594, 12.9722],
    [77.594, 12.971],
  ]],
};

describe('polygonCentroid', () => {
  it('puts the centre of a square at the middle of it', () => {
    const [lon, lat] = polygonCentroid(square);
    expect(lon).toBeCloseTo(77.5946, 6);
    expect(lat).toBeCloseTo(12.9716, 6);
  });

  it('is area-weighted, not the mean of the vertices', () => {
    // An L shape: the vertex mean and the true centroid are different points,
    // and a boundary walked with clustered GPS points looks like this.
    const lshape = {
      type: 'Polygon' as const,
      coordinates: [[
        [0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2], [0, 0],
      ]],
    };
    const [lon, lat] = polygonCentroid(lshape);
    // Area-weighted centroid of this L is (5/6, 5/6).
    expect(lon).toBeCloseTo(0.8333, 3);
    expect(lat).toBeCloseTo(0.8333, 3);

    const vertices = lshape.coordinates[0]!.slice(0, -1);
    const meanLon = vertices.reduce((s, p) => s + p[0]!, 0) / vertices.length;
    expect(meanLon).toBeCloseTo(1.0, 3);
    expect(lon).not.toBeCloseTo(meanLon, 3);
  });

  it('gives the same answer whether or not the ring is closed', () => {
    const open = {
      type: 'Polygon' as const,
      coordinates: [square.coordinates[0]!.slice(0, -1)],
    };
    expect(polygonCentroid(open)).toEqual(polygonCentroid(square));
  });

  it('is unchanged by winding direction', () => {
    const reversed = {
      type: 'Polygon' as const,
      coordinates: [[...square.coordinates[0]!].reverse()],
    };
    const [lon, lat] = polygonCentroid(reversed);
    const [lon2, lat2] = polygonCentroid(square);
    expect(lon).toBeCloseTo(lon2, 9);
    expect(lat).toBeCloseTo(lat2, 9);
  });

  it('falls back to the vertex mean on a degenerate ring instead of dividing by zero', () => {
    const collinear = {
      type: 'Polygon' as const,
      coordinates: [[[0, 0], [1, 1], [2, 2], [0, 0]]],
    };
    const [lon, lat] = polygonCentroid(collinear);
    expect(Number.isFinite(lon)).toBe(true);
    expect(Number.isFinite(lat)).toBe(true);
    expect(lon).toBeCloseTo(1, 6);
  });

  it('does not throw on an empty ring', () => {
    expect(polygonCentroid({ type: 'Polygon', coordinates: [[]] })).toEqual([0, 0]);
  });

  it('lands inside the field it describes', () => {
    const [lon, lat] = polygonCentroid(square);
    const ring = square.coordinates[0]!;
    const lons = ring.map((p) => p[0]!);
    const lats = ring.map((p) => p[1]!);
    expect(lon).toBeGreaterThan(Math.min(...lons));
    expect(lon).toBeLessThan(Math.max(...lons));
    expect(lat).toBeGreaterThan(Math.min(...lats));
    expect(lat).toBeLessThan(Math.max(...lats));
  });
});

describe('polygonAreaHa', () => {
  it('agrees with the hand-computed area of the test square', () => {
    // ~130 m x ~133 m at 13 degrees north.
    expect(polygonAreaHa(square)).toBeGreaterThan(1.6);
    expect(polygonAreaHa(square)).toBeLessThan(1.8);
  });

  it('is never negative, whichever way the boundary was walked', () => {
    const reversed = {
      type: 'Polygon' as const,
      coordinates: [[...square.coordinates[0]!].reverse()],
    };
    expect(polygonAreaHa(reversed)).toBeGreaterThan(0);
  });
});

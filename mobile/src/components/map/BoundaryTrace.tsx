/**
 * Live boundary trace, for both ways a field gets its edges.
 *
 * Walking: three things are drawn, in the order they matter to someone looking
 * at a phone in sunlight -- the path walked so far, a marker on the starting
 * corner, and, once they are close enough to finish, the line that would close
 * the loop. Seeing the closing line is what tells a farmer they have walked far
 * enough, without reading a number.
 *
 * Drawing: the same trace, plus `vertices` so every tapped corner is visible
 * and `closed` so the shape reads as an enclosed field rather than a path. A
 * walked point arrives from the GPS and cannot be aimed at; a drawn one was
 * placed deliberately and has to be visible to be corrected.
 */

import {
  CircleLayer, FillLayer, LineLayer, ShapeSource,
} from '@maplibre/maplibre-react-native';
import React, { useMemo } from 'react';

import { colors } from '../../constants/theme';
import type { Coordinate } from '../../types';

interface Props {
  coordinates: Coordinate[];
  canClose: boolean;
  /** Mark every point, not just the first. For tapped corners. */
  vertices?: boolean;
  /** Draw the ring joined up and shaded, rather than an open path. */
  closed?: boolean;
}

export const BoundaryTrace: React.FC<Props> = ({
  coordinates, canClose, vertices = false, closed = false,
}) => {
  const positions = useMemo(
    () => coordinates.map((c) => [c.longitude, c.latitude]),
    [coordinates],
  );

  if (positions.length === 0) return null;

  const start = positions[0]!;
  const last = positions[positions.length - 1]!;

  const walked = {
    type: 'Feature' as const,
    properties: {},
    geometry: {
      type: 'LineString' as const,
      // A drawn boundary is a closed ring from the moment it has three
      // corners; a walked one is an open path until the farmer gets back.
      coordinates: closed && positions.length >= 3 ? [...positions, start] : positions,
    },
  };

  const closing = {
    type: 'Feature' as const,
    properties: {},
    geometry: { type: 'LineString' as const, coordinates: [last, start] },
  };

  const startPoint = {
    type: 'Feature' as const,
    properties: {},
    geometry: { type: 'Point' as const, coordinates: start },
  };

  return (
    <>
      {positions.length >= 2 ? (
        <ShapeSource id="boundary-walked" shape={walked}>
          <LineLayer
            id="boundary-walked-line"
            style={{
              lineColor: colors.primary,
              lineWidth: 5,
              lineCap: 'round',
              lineJoin: 'round',
            }}
          />
        </ShapeSource>
      ) : null}

      {canClose && positions.length >= 3 ? (
        <ShapeSource id="boundary-closing" shape={closing}>
          <LineLayer
            id="boundary-closing-line"
            style={{
              lineColor: colors.primary,
              lineWidth: 3,
              lineDasharray: [2, 2],
              lineOpacity: 0.8,
            }}
          />
        </ShapeSource>
      ) : null}

      {closed && positions.length >= 3 ? (
        <ShapeSource
          id="boundary-ring"
          shape={{
            type: 'Feature',
            properties: {},
            geometry: { type: 'Polygon', coordinates: [[...positions, start]] },
          }}
        >
          <FillLayer
            id="boundary-ring-fill"
            // Light enough that the satellite basemap stays readable through
            // it: a farmer is matching this shape against the field they can
            // see, and a solid fill hides the thing being matched.
            style={{ fillColor: colors.primary, fillOpacity: 0.22 }}
          />
        </ShapeSource>
      ) : null}

      {vertices && positions.length > 0 ? (
        <ShapeSource
          id="boundary-vertices"
          shape={{
            type: 'FeatureCollection',
            features: positions.map((position) => ({
              type: 'Feature' as const,
              properties: {},
              geometry: { type: 'Point' as const, coordinates: position },
            })),
          }}
        >
          <CircleLayer
            id="boundary-vertex-dots"
            style={{
              circleRadius: 7,
              circleColor: colors.onPrimary,
              circleStrokeWidth: 3,
              circleStrokeColor: colors.primary,
            }}
          />
        </ShapeSource>
      ) : null}

      <ShapeSource id="boundary-start" shape={startPoint}>
        <CircleLayer
          id="boundary-start-dot"
          style={{
            circleRadius: 9,
            circleColor: canClose ? colors.primary : colors.watch,
            circleStrokeWidth: 3,
            circleStrokeColor: colors.onPrimary,
          }}
        />
      </ShapeSource>
    </>
  );
};

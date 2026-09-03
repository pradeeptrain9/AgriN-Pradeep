/**
 * Live boundary trace while the farmer walks.
 *
 * Three things are drawn, in the order they matter to someone looking at a
 * phone in sunlight: the path walked so far, a marker on the starting corner,
 * and -- once they are close enough to finish -- the line that would close the
 * loop. Seeing the closing line is what tells a farmer they have walked far
 * enough, without reading a number.
 */

import { CircleLayer, LineLayer, ShapeSource } from '@maplibre/maplibre-react-native';
import React, { useMemo } from 'react';

import { colors } from '../../constants/theme';
import type { Coordinate } from '../../types';

interface Props {
  coordinates: Coordinate[];
  canClose: boolean;
}

export const BoundaryTrace: React.FC<Props> = ({ coordinates, canClose }) => {
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
    geometry: { type: 'LineString' as const, coordinates: positions },
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

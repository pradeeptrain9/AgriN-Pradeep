import { FillLayer, LineLayer, ShapeSource } from '@maplibre/maplibre-react-native';
import React from 'react';

import { severityColor } from '../../constants/theme';
import type { GeoJsonPolygon } from '../../types';

interface Props {
  id: string;
  geometry: GeoJsonPolygon;
  severity?: string;
}

/**
 * A saved field. Filled by health status, but the fill is translucent so the
 * imagery underneath stays readable -- a farmer recognises their plot by what
 * is around it, not by its outline.
 */
export const FieldPolygon: React.FC<Props> = ({ id, geometry, severity }) => {
  const shape = { type: 'Feature' as const, properties: {}, geometry };
  const color = severityColor(severity);

  return (
    <ShapeSource id={`field-${id}`} shape={shape}>
      <FillLayer id={`field-fill-${id}`} style={{ fillColor: color, fillOpacity: 0.25 }} />
      <LineLayer
        id={`field-outline-${id}`}
        style={{ lineColor: color, lineWidth: 3, lineJoin: 'round' }}
      />
    </ShapeSource>
  );
};

/**
 * Map surface.
 *
 * Wraps MapLibre so no screen imports it directly: the map is an enhancement,
 * not a dependency. If tiles never load the children -- the boundary trace, the
 * field polygon -- still render over the background colour, which is the normal
 * case in a field with no signal.
 */

import { Camera, MapView, UserLocation } from '@maplibre/maplibre-react-native';
import React, { useRef } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { MAP_DEFAULTS } from '../../constants/config';
import { rasterStyle } from '../../constants/mapStyle';
import { colors, radius, spacing, type } from '../../constants/theme';
import { useDeviceTier } from '../../hooks/useDeviceTier';

interface Props {
  centerCoordinate?: [number, number];
  bounds?: { ne: [number, number]; sw: [number, number] };
  followUser?: boolean;
  zoom?: number;
  children?: React.ReactNode;
  attributionNote?: boolean;
}

export const FieldMapView: React.FC<Props> = ({
  centerCoordinate, bounds, followUser = false, zoom, children, attributionNote = true,
}) => {
  const cameraRef = useRef<any>(null);
  const tier = useDeviceTier();

  return (
    <View style={styles.container}>
      <MapView
        style={styles.map}
        mapStyle={rasterStyle()}
        logoEnabled={false}
        attributionEnabled={false}
        rotateEnabled={false}
        pitchEnabled={false}
        compassEnabled={tier.tier === 'high'}
      >
        <Camera
          ref={cameraRef}
          defaultSettings={{
            centerCoordinate: centerCoordinate ?? [78.9629, 20.5937],
            zoomLevel: zoom ?? MAP_DEFAULTS.zoom,
          }}
          centerCoordinate={followUser ? undefined : centerCoordinate}
          bounds={bounds ? { ne: bounds.ne, sw: bounds.sw, paddingTop: 40,
            paddingBottom: 40, paddingLeft: 40, paddingRight: 40 } : undefined}
          followUserLocation={followUser}
          followZoomLevel={MAP_DEFAULTS.zoom}
          maxZoomLevel={tier.mapMaxZoom}
          minZoomLevel={MAP_DEFAULTS.minZoom}
          // Animation costs frames on a 2 GB phone while GPS is also running.
          animationDuration={tier.animationsEnabled ? 500 : 0}
        />
        {followUser ? <UserLocation visible renderMode="normal" /> : null}
        {children}
      </MapView>

      {attributionNote ? (
        <Text style={styles.attribution}>© OpenStreetMap contributors</Text>
      ) : null}
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, borderRadius: radius.md, overflow: 'hidden', backgroundColor: '#E8EDE6' },
  map: { flex: 1 },
  attribution: {
    ...type.caption,
    position: 'absolute', bottom: spacing.xs, right: spacing.xs,
    fontSize: 10, color: colors.textMuted,
    backgroundColor: 'rgba(255,255,255,0.7)', paddingHorizontal: 4, borderRadius: 3,
  },
});

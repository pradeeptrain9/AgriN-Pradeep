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
import { useBasemap } from '../../hooks/useBasemap';
import { useDeviceTier } from '../../hooks/useDeviceTier';

interface Props {
  centerCoordinate?: [number, number];
  bounds?: { ne: [number, number]; sw: [number, number] };
  followUser?: boolean;
  zoom?: number;
  children?: React.ReactNode;
  attributionNote?: boolean;
  /** Called with [longitude, latitude] when the farmer taps the map. */
  onPress?: (position: [number, number]) => void;
}

export const FieldMapView: React.FC<Props> = ({
  centerCoordinate, bounds, followUser = false, zoom, children,
  attributionNote = true, onPress,
}) => {
  const cameraRef = useRef<any>(null);
  const tier = useDeviceTier();
  const basemap = useBasemap();

  return (
    <View style={styles.container}>
      <MapView
        style={styles.map}
        mapStyle={rasterStyle(basemap.tile_url, basemap.attribution, basemap.max_zoom)}
        // MapLibre hands back a GeoJSON Feature. Unwrapping it here keeps every
        // screen dealing in plain [lon, lat] positions, which is what the geo
        // utilities and the GeoJSON ring both already speak.
        onPress={
          onPress
            ? (feature: any) => {
                const position = feature?.geometry?.coordinates;
                if (Array.isArray(position) && position.length >= 2) {
                  onPress([Number(position[0]), Number(position[1])]);
                }
              }
            : undefined
        }
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
        <Text style={styles.attribution}>{basemap.attribution}</Text>
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

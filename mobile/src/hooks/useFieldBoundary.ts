/**
 * Walk-the-boundary field mapping.
 *
 * Ported from Conquerer's useLocationTracking: the mechanic is identical --
 * follow the walker with GPS, accumulate a track, close it into a polygon --
 * but the tuning is not. A run is kilometres long and a 20 m fix is tolerable;
 * a field edge is metres wide, so points worse than 15 m are discarded and the
 * minimum spacing drops from 3 m to 1.5 m.
 *
 * The GPS watch lives at module scope and runs under a foreground service so it
 * survives the screen unmounting and the phone going into a pocket mid-walk.
 */

import { useCallback, useEffect } from 'react';
import { Alert, PermissionsAndroid, Platform } from 'react-native';
import BackgroundService from 'react-native-background-actions';
import Geolocation from 'react-native-geolocation-service';

import { FIELD_LIMITS, GPS_CONFIG } from '../constants/config';
import { useBoundaryStore } from '../store/boundarySlice';
import { distanceToStart, haversineDistance, pathLength, validateBoundary } from '../utils/geo';
import type { Coordinate } from '../types';

const requestLocationPermission = async (): Promise<boolean> => {
  if (Platform.OS !== 'android') return true;

  const fine = await PermissionsAndroid.request(
    PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION!,
    {
      title: 'Location for field mapping',
      message: 'AgriN needs GPS to trace the edge of your field as you walk it.',
      buttonPositive: 'Allow',
    },
  );
  if (fine !== PermissionsAndroid.RESULTS.GRANTED) return false;

  if (Number(Platform.Version) >= 33) {
    await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS!, {
      title: 'Notifications',
      message: 'Allow a notification so mapping continues with the screen off.',
      buttonPositive: 'Allow',
    });
  }
  return true;
};

let watchId: number | null = null;

const stopGpsWatch = (): void => {
  if (watchId !== null) {
    Geolocation.clearWatch(watchId);
    watchId = null;
  }
};

const startGpsWatch = (): void => {
  stopGpsWatch();
  watchId = Geolocation.watchPosition(
    (position) => {
      const { latitude, longitude, accuracy } = position.coords;
      const store = useBoundaryStore.getState();

      // A poor fix would put a false corner on the boundary; count it so the UI
      // can tell the farmer to wait rather than silently doing nothing.
      if (accuracy != null && accuracy > GPS_CONFIG.maxAcceptableAccuracyM) {
        store.noteAccuracy(accuracy, true);
        return;
      }
      store.noteAccuracy(accuracy ?? null, false);

      const coords = store.coordinates;
      if (coords.length > 0) {
        const last = coords[coords.length - 1]!;
        const step = haversineDistance(last.latitude, last.longitude, latitude, longitude);
        if (step < GPS_CONFIG.minPointSpacingM) return;
      }

      const point: Coordinate = {
        latitude, longitude, timestamp: position.timestamp,
        accuracy: accuracy ?? undefined,
      };
      store.addCoordinate(point);

      const fresh = useBoundaryStore.getState().coordinates;
      store.setMetrics(pathLength(fresh), distanceToStart(fresh));
    },
    (error) => {
      console.warn('GPS error', error.message);
    },
    {
      enableHighAccuracy: GPS_CONFIG.enableHighAccuracy,
      distanceFilter: GPS_CONFIG.distanceFilter,
      interval: GPS_CONFIG.interval,
      fastestInterval: GPS_CONFIG.fastestInterval,
      forceRequestLocation: true,
      showLocationDialog: true,
    },
  );
};

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

const backgroundTask = async (): Promise<void> => {
  startGpsWatch();
  while (BackgroundService.isRunning()) {
    const { coordinates, perimeterM } = useBoundaryStore.getState();
    try {
      await BackgroundService.updateNotification({
        taskDesc: `${coordinates.length} points · ${Math.round(perimeterM)} m walked`,
      });
    } catch {
      // Notification updates are cosmetic.
    }
    await sleep(2000);
  }
};

const backgroundOptions = {
  taskName: 'AgriNFieldMapping',
  taskTitle: 'Mapping your field',
  taskDesc: 'Walk along the edge of the field',
  taskIcon: { name: 'ic_launcher', type: 'mipmap' },
  color: '#1B7F3B',
  linkingURI: 'agrin://map-field',
};

export const useFieldBoundary = () => {
  const {
    isWalking, coordinates, perimeterM, distanceToStartM, lastAccuracyM,
    rejectedForAccuracy, start, stop, reset, undoLast,
  } = useBoundaryStore();

  const startWalking = useCallback(async (): Promise<boolean> => {
    const granted = await requestLocationPermission();
    if (!granted) {
      Alert.alert(
        'Location needed',
        'AgriN cannot trace your field without GPS. You can also draw the field by hand on the map.',
      );
      return false;
    }

    start();
    try {
      if (!BackgroundService.isRunning()) {
        await BackgroundService.start(backgroundTask, backgroundOptions);
      } else {
        startGpsWatch();
      }
    } catch (error) {
      // Foreground service refused (some OEM builds); GPS still works while the
      // screen is on, which is enough to walk one boundary.
      console.warn('foreground service unavailable', error);
      startGpsWatch();
    }
    return true;
  }, [start]);

  const stopWalking = useCallback(async (): Promise<void> => {
    stopGpsWatch();
    try {
      if (BackgroundService.isRunning()) await BackgroundService.stop();
    } catch (error) {
      console.warn('could not stop background service', error);
    }
    stop();
  }, [stop]);

  const cancel = useCallback(async (): Promise<void> => {
    await stopWalking();
    reset();
  }, [reset, stopWalking]);

  /** True once the walker is back near where they began. */
  const canClose =
    coordinates.length >= FIELD_LIMITS.minPoints &&
    perimeterM >= FIELD_LIMITS.autoCloseMinPerimeterM &&
    distanceToStartM <= FIELD_LIMITS.autoCloseRadiusM;

  const validation = validateBoundary(coordinates, FIELD_LIMITS);

  // Tracking must outlive this component: no teardown on unmount.
  useEffect(() => () => {}, []);

  return {
    isWalking, coordinates, perimeterM, distanceToStartM, lastAccuracyM,
    rejectedForAccuracy, canClose, validation,
    startWalking, stopWalking, cancel, undoLast, reset,
  };
};

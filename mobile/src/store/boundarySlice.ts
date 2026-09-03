import { create } from 'zustand';

import type { Coordinate } from '../types';

/**
 * State for a boundary walk in progress.
 *
 * Module-level store rather than component state because the GPS watch runs
 * inside a foreground service and must keep writing points while the screen is
 * unmounted or the phone is in a pocket.
 */
interface BoundaryStore {
  isWalking: boolean;
  coordinates: Coordinate[];
  perimeterM: number;
  distanceToStartM: number;
  lastAccuracyM: number | null;
  rejectedForAccuracy: number;
  startedAt: number | null;

  start: () => void;
  stop: () => void;
  reset: () => void;
  addCoordinate: (coord: Coordinate) => void;
  undoLast: () => void;
  setMetrics: (perimeterM: number, distanceToStartM: number) => void;
  noteAccuracy: (accuracy: number | null, rejected: boolean) => void;
}

export const useBoundaryStore = create<BoundaryStore>((set) => ({
  isWalking: false,
  coordinates: [],
  perimeterM: 0,
  distanceToStartM: 0,
  lastAccuracyM: null,
  rejectedForAccuracy: 0,
  startedAt: null,

  start: () =>
    set({
      isWalking: true, coordinates: [], perimeterM: 0, distanceToStartM: 0,
      rejectedForAccuracy: 0, startedAt: Date.now(),
    }),

  stop: () => set({ isWalking: false }),

  reset: () =>
    set({
      isWalking: false, coordinates: [], perimeterM: 0, distanceToStartM: 0,
      lastAccuracyM: null, rejectedForAccuracy: 0, startedAt: null,
    }),

  addCoordinate: (coord) =>
    set((state) => ({ coordinates: [...state.coordinates, coord] })),

  // A wrong point usually means the walker stepped off the bund; let them drop it.
  undoLast: () =>
    set((state) => ({ coordinates: state.coordinates.slice(0, -1) })),

  setMetrics: (perimeterM, distanceToStartM) => set({ perimeterM, distanceToStartM }),

  noteAccuracy: (accuracy, rejected) =>
    set((state) => ({
      lastAccuracyM: accuracy,
      rejectedForAccuracy: rejected
        ? state.rejectedForAccuracy + 1
        : state.rejectedForAccuracy,
    })),
}));

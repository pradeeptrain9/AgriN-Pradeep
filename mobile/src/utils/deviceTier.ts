/**
 * Device tiering.
 *
 * One APK, one tested baseline, progressive enhancement on top. The low path is
 * the default and the one that is actually exercised, so a misdetected mid-range
 * phone degrades to something that works rather than something that breaks.
 *
 * What the tier changes:
 *   low   fewer map zoom levels, no animation, 448 px capture
 *   high  deeper map zoom, animation, 640 px capture
 *
 * Capture sizes are deliberately close to the classifier's 224 px input. A
 * larger capture is resampled twice -- once by the picker, once by us -- and
 * each pass costs confidence measurably.
 *
 * Inference is CPU-only on both tiers: the GPU delegate library was removed from
 * the build because it cost 1.25 MB to accelerate an already-fast int8 model.
 *
 * Note: both tiers use the SAME raster basemap. Vector tiles were in the plan
 * but every hosted vector style requires an API key, which a digital public good
 * should not depend on. Serving our own PMTiles basemap is the route to vector,
 * and it is a node deployment change rather than a device-tier one.
 */

import DeviceInfo from 'react-native-device-info';
import { Platform } from 'react-native';

import type { DeviceTier } from '../types';

const HIGH_TIER_MIN_RAM_BYTES = 4 * 1024 * 1024 * 1024;
const HIGH_TIER_MIN_ANDROID_API = 29; // Android 10

let cached: DeviceTier | null = null;
let override: DeviceTier | null = null;

export const setTierOverride = (tier: DeviceTier | null): void => {
  override = tier;
  cached = null;
};

export const detectTier = async (): Promise<DeviceTier> => {
  if (override) return override;
  if (cached) return cached;

  try {
    const ram = await DeviceInfo.getTotalMemory();
    const apiLevel = Platform.OS === 'android' ? Number(Platform.Version) : 999;
    const lowRamDevice = DeviceInfo.isLowRamDevice();

    const high =
      !lowRamDevice && ram >= HIGH_TIER_MIN_RAM_BYTES && apiLevel >= HIGH_TIER_MIN_ANDROID_API;
    cached = high ? 'high' : 'low';
  } catch {
    // Detection failed: assume the constrained device. Being wrong in this
    // direction costs polish; being wrong the other way costs usability.
    cached = 'low';
  }
  return cached;
};

export interface TierProfile {
  tier: DeviceTier;
  animationsEnabled: boolean;
  captureMaxPx: number;
  captureQuality: number;
  mapMaxZoom: number;
  listWindowSize: number;
}

export const profileFor = (tier: DeviceTier): TierProfile =>
  tier === 'high'
    ? {
        tier, animationsEnabled: true,
        captureMaxPx: 640, captureQuality: 0.9,
        mapMaxZoom: 19, listWindowSize: 21,
      }
    : {
        tier, animationsEnabled: false,
        captureMaxPx: 448, captureQuality: 0.85,
        mapMaxZoom: 17, listWindowSize: 7,
      };

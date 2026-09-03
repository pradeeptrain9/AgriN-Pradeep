import { useEffect, useState } from 'react';

import { detectTier, profileFor, type TierProfile } from '../utils/deviceTier';

/** Low tier is the initial value so the first paint is always the safe one. */
export const useDeviceTier = (): TierProfile => {
  const [profile, setProfile] = useState<TierProfile>(profileFor('low'));

  useEffect(() => {
    let active = true;
    detectTier().then((tier) => {
      if (active) setProfile(profileFor(tier));
    });
    return () => {
      active = false;
    };
  }, []);

  return profile;
};

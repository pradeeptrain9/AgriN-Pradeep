/**
 * The basemap a map surface should draw right now.
 *
 * Starts from whatever this phone already has -- cache, then the keyless
 * OpenStreetMap default -- and swaps in the node's answer when it arrives.
 * Nothing here ever leaves the caller without a basemap, because a map with no
 * tile URL is a grey rectangle and there is always a working one to hand.
 */

import { useEffect, useState } from 'react';

import { DEFAULT_BASEMAP, cachedBasemap, resolveBasemap } from '../services/basemap';
import type { Basemap } from '../types';

export const useBasemap = (): Basemap => {
  const [basemap, setBasemap] = useState<Basemap>(DEFAULT_BASEMAP);

  useEffect(() => {
    let live = true;

    // Two stages on purpose. The cached read is local and returns in
    // milliseconds, so the map is showing the right tiles before the node --
    // which may be asleep and take most of a minute to wake -- has answered.
    (async () => {
      const cached = await cachedBasemap();
      if (live && cached) setBasemap(cached);
      const resolved = await resolveBasemap();
      if (live) setBasemap(resolved);
    })();

    return () => {
      live = false;
    };
  }, []);

  return basemap;
};

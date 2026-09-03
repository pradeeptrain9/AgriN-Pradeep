import { create } from 'zustand';

import { loadFields as loadCached, saveFields, type CachedField } from '../db';
import { listFields } from '../services/api';
import { isOffline } from '../services/api';
import type { Field } from '../types';

interface FieldStore {
  fields: CachedField[];
  isLoading: boolean;
  isStale: boolean;
  error: string | null;
  loadCached: () => void;
  refresh: () => Promise<void>;
  upsert: (field: Field) => void;
}

export const useFieldStore = create<FieldStore>((set, get) => ({
  fields: [],
  isLoading: false,
  isStale: false,
  error: null,

  /** Read the mirror first so the list paints instantly, signal or not. */
  loadCached: () => {
    try {
      set({ fields: loadCached() });
    } catch {
      set({ fields: [] });
    }
  },

  refresh: async () => {
    set({ isLoading: true, error: null });
    try {
      const fields = await listFields();
      saveFields(fields);
      // Read back through the mirror rather than showing the server's list
      // directly. A field mapped offline is not in that list until the outbox
      // drains, and the two run concurrently -- so using the response as the
      // whole truth makes the farmer's field disappear the moment signal
      // returns, which is exactly when they are most likely to be looking.
      set({ fields: loadCached(), isLoading: false, isStale: false });
    } catch (error) {
      // Offline is not an error state: keep showing the cache, flag it as stale.
      set({
        isLoading: false,
        isStale: true,
        error: isOffline(error) ? null : 'Could not refresh your fields.',
      });
      if (get().fields.length === 0) get().loadCached();
    }
  },

  upsert: (field) => {
    saveFields([field]);
    set((state) => ({
      fields: [
        { ...field, pending: false },
        ...state.fields.filter((f) => f.id !== field.id),
      ],
    }));
  },
}));

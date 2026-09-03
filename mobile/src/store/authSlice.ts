import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';

interface AuthStore {
  userId: string | null;
  accessToken: string | null;
  lang: string;
  isAuthenticated: boolean;
  isLoading: boolean;
  /** Null until the farmer has read what happens to their data. */
  consentAcceptedAt: string | null;
  acceptConsent: () => Promise<void>;
  setAuth: (userId: string, accessToken: string) => Promise<void>;
  setLang: (lang: string) => Promise<void>;
  logout: () => Promise<void>;
  loadFromStorage: () => Promise<void>;
}

const KEYS = {
  user: '@agrin_user_id', token: '@agrin_token', lang: '@agrin_lang',
  consent: '@agrin_consent_at',
};

export const useAuthStore = create<AuthStore>((set) => ({
  userId: null,
  accessToken: null,
  lang: 'en',
  isAuthenticated: false,
  isLoading: true,
  consentAcceptedAt: null,

  acceptConsent: async () => {
    const now = new Date().toISOString();
    await AsyncStorage.setItem(KEYS.consent, now);
    set({ consentAcceptedAt: now });
  },

  setAuth: async (userId, accessToken) => {
    await AsyncStorage.multiSet([[KEYS.user, userId], [KEYS.token, accessToken]]);
    set({ userId, accessToken, isAuthenticated: true, isLoading: false });
  },

  setLang: async (lang) => {
    await AsyncStorage.setItem(KEYS.lang, lang);
    set({ lang });
  },

  logout: async () => {
    // The offline mirror survives sign-out; only credentials are cleared.
    // Consent is cleared too: the next person to sign in on this phone has not
    // agreed to anything, and a shared handset is the normal case.
    await AsyncStorage.multiRemove([KEYS.user, KEYS.token, KEYS.consent]);
    set({
      userId: null, accessToken: null, isAuthenticated: false,
      isLoading: false, consentAcceptedAt: null,
    });
  },

  loadFromStorage: async () => {
    try {
      const entries = await AsyncStorage.multiGet([
        KEYS.user, KEYS.token, KEYS.lang, KEYS.consent,
      ]);
      const values = new Map(entries.map(([key, value]) => [key, value]));
      const userId = values.get(KEYS.user) ?? null;
      const token = values.get(KEYS.token) ?? null;
      set({
        userId,
        accessToken: token,
        lang: values.get(KEYS.lang) ?? 'en',
        consentAcceptedAt: values.get(KEYS.consent) ?? null,
        isAuthenticated: Boolean(userId && token),
        isLoading: false,
      });
    } catch {
      set({ isLoading: false });
    }
  },
}));

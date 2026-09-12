/**
 * HTTP client.
 *
 * Simpler than Conquerer's: this backend issues one long-lived JWT from phone
 * OTP and has no refresh endpoint, so a 401 means sign in again rather than
 * silently rotating a token that does not exist.
 */

import axios, { AxiosError } from 'axios';

import { useAuthStore } from '../store/authSlice';
import { currentNodeUrl } from './node';
import type {
  Advisory, CropSuggestions, Diagnosis, Field, FieldSource, FieldWeather, GeoJsonPolygon,
  Narration,
} from '../types';

/**
 * Long enough to survive a sleeping node waking up.
 *
 * The India node runs on free hosting that stops the service after 15 minutes
 * idle and takes 30-60 seconds to answer the request that wakes it. At the old
 * 30 s this aborted every time, and an aborted request carries no HTTP
 * response -- so `isOffline` called it offline and the farmer was told "No
 * internet connection" while holding a phone with four bars. The first tap
 * after a quiet afternoon is exactly when a farmer decides whether this app
 * works.
 *
 * A genuine lack of signal does not wait this long: DNS and connect failures
 * come back in seconds, so this ceiling is only reached by a server that is
 * slow rather than absent.
 */
const REQUEST_TIMEOUT_MS = 60000;

export const api = axios.create({
  timeout: REQUEST_TIMEOUT_MS,
  headers: { 'Content-Type': 'application/json' },
});

/** Aborted on our side after REQUEST_TIMEOUT_MS, rather than refused. */
export const isTimeout = (error: unknown): boolean => {
  const axiosError = error as AxiosError;
  return axiosError?.code === 'ECONNABORTED' || axiosError?.code === 'ETIMEDOUT';
};

api.interceptors.request.use((config) => {
  // Resolved per request rather than at module load, so changing node takes
  // effect immediately and no stale base URL survives a sign-out.
  config.baseURL = currentNodeUrl();
  const { accessToken } = useAuthStore.getState();
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`;
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) useAuthStore.getState().logout();

    // Retry a timeout exactly once. A sleeping node is woken by the request
    // that times out, so the second attempt usually meets a server that is
    // already up and answers immediately. Only once, and only for timeouts:
    // retrying a refused connection just doubles the wait before a farmer is
    // told something true, and retrying anything with a response would replay
    // writes the server already accepted.
    const config = error.config as (typeof error.config & { _retried?: boolean });
    if (config && !config._retried && isTimeout(error)) {
      config._retried = true;
      return api.request(config);
    }

    return Promise.reject(error);
  },
);

/** True when the failure was the network, not the server rejecting us. */
export const isOffline = (error: unknown): boolean => {
  const axiosError = error as AxiosError;
  return Boolean(axiosError?.isAxiosError && !axiosError.response);
};

export const readableError = (error: unknown, fallback: string): string => {
  const axiosError = error as AxiosError<{ detail?: string }>;
  // Checked before isOffline, which is also true for a timeout: "no internet"
  // is the wrong thing to tell someone whose signal is fine and whose server
  // is asleep, and it is the message that makes them stop trying.
  if (isTimeout(error)) {
    return 'The node is taking a long time to answer. It may be waking up — '
      + 'try again in a moment.';
  }
  if (isOffline(error)) return 'No internet connection. Saved on your phone.';
  return axiosError?.response?.data?.detail ?? fallback;
};

// -------------------------------------------------------------------- auth
export const requestOtp = async (phone: string) => {
  const { data } = await api.post('/auth/otp/request', { phone });
  return data as { sent: boolean; expires_in: number; dev_code?: string };
};

export const verifyOtp = async (phone: string, code: string) => {
  const { data } = await api.post('/auth/otp/verify', { phone, code });
  return data as { access_token: string; user_id: string; is_new_user: boolean };
};

// ------------------------------------------------------------------ fields
export const listFields = async (): Promise<Field[]> => {
  const { data } = await api.get('/fields');
  return data;
};

export const createField = async (
  name: string,
  geometry: GeoJsonPolygon,
  source: FieldSource = 'walked',
): Promise<Field> => {
  const { data } = await api.post('/fields', { name, geometry, source });
  return data;
};

export const setCrop = async (
  fieldId: string,
  body: { crop_code: string; sowing_date: string; previous_crop?: string | null },
) => {
  const { data } = await api.post(`/fields/${fieldId}/crop`, body);
  return data;
};

export const setSoilCard = async (fieldId: string, body: Record<string, unknown>) => {
  const { data } = await api.put(`/fields/${fieldId}/soil/card`, body);
  return data;
};

export const refreshField = async (fieldId: string) => {
  const { data } = await api.post(`/fields/${fieldId}/refresh`);
  return data;
};

export const getAdvisory = async (fieldId: string): Promise<Advisory> => {
  const { data } = await api.get(`/fields/${fieldId}/advisory`);
  return data;
};

export const getNarratedAdvisory = async (
  fieldId: string, lang: string,
): Promise<{ advisory: Advisory; narration: Narration }> => {
  const { data } = await api.get(`/fields/${fieldId}/advisory/narrated`, { params: { lang } });
  return data;
};

export const listCrops = async () => {
  const { data } = await api.get('/crops');
  return data as Array<{ code: string; label: string; season_days: number; fixes_nitrogen: boolean }>;
};

// --------------------------------------------------------------- diagnoses
export const submitDiagnosis = async (params: {
  cropCode: string;
  imageUri: string;
  fieldId?: string | null;
  predictions?: Array<{ class_code: string; probability: number }>;
}): Promise<Diagnosis> => {
  const form = new FormData();
  form.append('crop_code', params.cropCode);
  if (params.fieldId) form.append('field_id', params.fieldId);
  if (params.predictions?.length) {
    form.append('on_device_predictions', JSON.stringify(params.predictions));
  }
  form.append('image', {
    uri: params.imageUri,
    name: 'leaf.jpg',
    type: 'image/jpeg',
  } as unknown as Blob);

  const { data } = await api.post('/diagnoses', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
  return data;
};

export const listDiagnoses = async (): Promise<Diagnosis[]> => {
  const { data } = await api.get('/diagnoses');
  return data;
};

export const diseaseClasses = async (cropCode: string) => {
  const { data } = await api.get('/diagnoses/classes', { params: { crop_code: cropCode } });
  return data as {
    crop_code: string; supported: boolean; dataset: string | null;
    classes: Array<{ code: string; label: string; is_healthy: boolean }>;
  };
};

// ------------------------------------------------------------------ feedback
export type Verdict = 'helpful' | 'unclear' | 'wrong' | 'harmful';

export const submitFeedback = async (body: {
  kind: 'advisory' | 'diagnosis';
  verdict: Verdict;
  field_id?: string | null;
  diagnosis_id?: string | null;
  corrected_label?: string | null;
  comment?: string | null;
}) => {
  const { data } = await api.post('/feedback', body);
  return data as { id: string; escalated: boolean; message: string };
};

export const getFieldWeather = async (fieldId: string, days = 7) => {
  const { data } = await api.get(`/fields/${fieldId}/weather`, {
    params: { days },
  });
  return data as FieldWeather;
};

export const getCropSuggestions = async (fieldId: string) => {
  const { data } = await api.get(`/fields/${fieldId}/crop-suggestions`);
  return data as CropSuggestions;
};

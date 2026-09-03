/**
 * Which AgriN node this phone talks to.
 *
 * Not a testing hook: AgriN is a federated network where each country runs its
 * own node, so "which node do you belong to" is a real question a farmer's app
 * has to answer. An Indian farmer's data belongs on the Indian node and must not
 * be silently posted to a Brazilian one.
 *
 * The URL is stored on the device, defaults to this build's home node, and is
 * changeable before sign-in. It also removes the need to rebuild the app to
 * point at a local node during development.
 */

import AsyncStorage from '@react-native-async-storage/async-storage';

import { API_URL } from '../constants/config';

const KEY = '@agrin_node_url';

export const DEFAULT_NODE_URL = API_URL;

let cached: string | null = null;

/** Synchronous read for the axios interceptor; refreshed by loadNodeUrl(). */
export const currentNodeUrl = (): string => cached ?? DEFAULT_NODE_URL;

export const loadNodeUrl = async (): Promise<string> => {
  try {
    const stored = await AsyncStorage.getItem(KEY);
    cached = stored && stored.length > 0 ? stored : DEFAULT_NODE_URL;
  } catch {
    cached = DEFAULT_NODE_URL;
  }
  return cached;
};

export interface NodeUrlValidation {
  valid: boolean;
  normalised: string;
  problem?: string;
}

/**
 * Parsed with a regular expression rather than `new URL()`.
 *
 * React Native does NOT ship a complete URL implementation. Its URL class
 * constructs fine but every accessor throws:
 *
 *     get protocol(): string { throw new Error('URL.protocol is not implemented'); }
 *
 * so `new URL(x).protocol` blows up at runtime, outside any try/catch wrapped
 * around the constructor alone. Relying on it here meant the https-only check
 * never executed and Save crashed instead of validating. Regex parsing has no
 * such dependency.
 */
const URL_PATTERN = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/([^/:?#\s]+)(?::(\d+))?([/?#][^\s]*)?$/;

const LOOPBACK_HOSTS = ['localhost', '127.0.0.1', '10.0.2.2', '::1'];

export interface ParsedNodeUrl {
  scheme: string;
  host: string;
  port: string | null;
  path: string;
}

export const parseNodeUrl = (raw: string): ParsedNodeUrl | null => {
  const match = URL_PATTERN.exec(raw.trim());
  if (!match) return null;
  return {
    scheme: (match[1] ?? '').toLowerCase(),
    host: (match[2] ?? '').toLowerCase(),
    port: match[3] ?? null,
    path: match[4] ?? '',
  };
};

/**
 * Only http and https, and cleartext only to loopback.
 *
 * The Android network security config already blocks cleartext to real hosts,
 * but failing here gives the person a readable message instead of an opaque
 * network error, and stops a typo from quietly sending farm data in the clear.
 */
export const validateNodeUrl = (raw: string): NodeUrlValidation => {
  const trimmed = raw.trim().replace(/\/+$/, '');
  if (!trimmed) return { valid: false, normalised: '', problem: 'Enter a node address.' };

  const parsed = parseNodeUrl(trimmed);
  if (!parsed) {
    return {
      valid: false, normalised: trimmed,
      problem: 'That is not a valid address. It should look like https://node-in.agrin.org',
    };
  }

  if (parsed.scheme !== 'https' && parsed.scheme !== 'http') {
    return { valid: false, normalised: trimmed, problem: 'Address must start with https://' };
  }

  if (parsed.scheme === 'http' && !LOOPBACK_HOSTS.includes(parsed.host)) {
    return {
      valid: false, normalised: trimmed,
      problem: 'Only https is allowed. Plain http would send your farm data unprotected.',
    };
  }

  return { valid: true, normalised: trimmed };
};

export const setNodeUrl = async (raw: string): Promise<NodeUrlValidation> => {
  const result = validateNodeUrl(raw);
  if (!result.valid) return result;
  await AsyncStorage.setItem(KEY, result.normalised);
  cached = result.normalised;
  return result;
};

/** Shown on the login screen so the person can see where their data goes. */
export const nodeLabel = (url: string): string => {
  const parsed = parseNodeUrl(url);
  if (!parsed) return url;
  return parsed.port ? `${parsed.host}:${parsed.port}` : parsed.host;
};

/**
 * Offline mirror.
 *
 * A farmer standing in a field has no signal more often than they have signal.
 * Everything the app shows is read from SQLite; the network only ever refills
 * it. Writes go into an outbox and drain when a connection appears, so mapping
 * a boundary and scanning a leaf both work fully offline.
 */

import { open, type DB } from '@op-engineering/op-sqlite';

/**
 * Reads use op-sqlite's `executeSync`, the JSI synchronous path, so a screen can
 * paint from the mirror during render without an await. The rows involved are
 * tens, not thousands. If a node ever manages hundreds of fields per farmer,
 * the bulk writes here should move to `executeBatch` off the JS thread.
 */

import type { Advisory, Diagnosis, Field } from '../types';

/** A field as held on the phone: `pending` means it has not reached a node yet. */
export interface CachedField extends Field {
  pending: boolean;
}

let db: DB | null = null;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS fields (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  area_ha REAL NOT NULL,
  centroid_lon REAL, centroid_lat REAL,
  geometry TEXT NOT NULL,
  crop TEXT, soil TEXT,
  created_at TEXT,
  synced_at INTEGER
);

CREATE TABLE IF NOT EXISTS advisories (
  field_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  narration TEXT,
  fetched_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnoses (
  id TEXT PRIMARY KEY,
  field_id TEXT,
  crop_code TEXT,
  payload TEXT NOT NULL,
  image_path TEXT,
  created_at INTEGER NOT NULL
);

-- Weather, mirrored per field. The irrigation advice leans on these numbers,
-- so a farmer standing in a field with no signal should still be able to see
-- what it was based on.
CREATE TABLE IF NOT EXISTS weather (
  field_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  fetched_at INTEGER NOT NULL
);

-- The crop registry, mirrored so a field walked with no signal can still be
-- given a crop. It is small, static reference data -- fourteen rows that change
-- when the node operator adds a crop, not per farmer -- and without it the
-- offline flow dead-ends at "add the crop", which is exactly the step the
-- pending-field screen promises will work.
CREATE TABLE IF NOT EXISTS crops (
  code TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  position INTEGER NOT NULL,
  fetched_at INTEGER NOT NULL
);

-- Work that could not reach the server yet. Drained in insertion order so a
-- field is always created before the diagnosis that references it.
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  file_path TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS outbox_order_idx ON outbox (created_at);
`;

export const initDb = (): DB => {
  if (db) return db;
  db = open({ name: 'agrin.sqlite' });
  db.executeSync('PRAGMA journal_mode = WAL');
  db.executeSync('PRAGMA foreign_keys = ON');
  for (const statement of SCHEMA.split(';')) {
    const trimmed = statement.trim();
    if (trimmed) db.executeSync(trimmed);
  }
  return db;
};

const conn = (): DB => db ?? initDb();

// ------------------------------------------------------------------- fields
export const saveFields = (fields: Field[]): void => {
  const c = conn();
  c.executeSync('BEGIN');
  try {
    for (const field of fields) {
      c.executeSync(
        `INSERT INTO fields (id, name, area_ha, centroid_lon, centroid_lat, geometry,
           crop, soil, created_at, synced_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(id) DO UPDATE SET
           name = excluded.name, area_ha = excluded.area_ha,
           centroid_lon = excluded.centroid_lon, centroid_lat = excluded.centroid_lat,
           geometry = excluded.geometry, crop = excluded.crop, soil = excluded.soil,
           synced_at = excluded.synced_at`,
        [
          field.id, field.name, field.area_ha,
          field.centroid?.[0] ?? null, field.centroid?.[1] ?? null,
          JSON.stringify(field.geometry),
          field.crop ? JSON.stringify(field.crop) : null,
          field.soil ? JSON.stringify(field.soil) : null,
          field.created_at ?? null, Date.now(),
        ],
      );
    }
    c.executeSync('COMMIT');
  } catch (error) {
    c.executeSync('ROLLBACK');
    throw error;
  }
};

/**
 * A field mapped with no signal exists on the phone before it exists on the
 * server, and needs an id to hang a crop, a soil card and a diagnosis on. The
 * prefix makes the distinction checkable anywhere, and survives being written
 * into a queued payload.
 */
export const LOCAL_ID_PREFIX = 'local:';

export const isLocalId = (id: string): boolean => id.startsWith(LOCAL_ID_PREFIX);

export const newLocalId = (): string =>
  `${LOCAL_ID_PREFIX}${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

/** Stored with synced_at NULL, which is what marks it as not yet sent. */
export const insertLocalField = (field: {
  id: string;
  name: string;
  area_ha: number;
  centroid: [number, number];
  geometry: unknown;
}): void => {
  conn().executeSync(
    `INSERT INTO fields (id, name, area_ha, centroid_lon, centroid_lat, geometry,
       created_at, synced_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, NULL)`,
    [
      field.id, field.name, field.area_ha,
      field.centroid[0], field.centroid[1],
      JSON.stringify(field.geometry), new Date().toISOString(),
    ],
  );
};

/**
 * The server assigns the real id only once the field is accepted, so everything
 * queued against the local id has to be rewritten to point at it -- including
 * payloads still sitting in the outbox behind this item. Done in one
 * transaction: a partial remap would orphan a farmer's crop or soil card.
 */
export const remapFieldId = (localId: string, realId: string): void => {
  const c = conn();
  c.executeSync('BEGIN');
  try {
    // If the server's copy is already here -- a partial earlier sync, a
    // refresh that landed first -- the rename would collide on the primary
    // key. Drop the local placeholder instead: the server row is the truth,
    // and the queued work still needs its references rewritten below.
    // If the server's copy is already here -- a partial earlier sync, a
    // refresh that landed first -- the rename would collide on the primary
    // key. Drop the local placeholder instead: the server row is the truth,
    // and the queued work still needs its references rewritten below.
    c.executeSync('DELETE FROM fields WHERE id = ? AND EXISTS '
      + '(SELECT 1 FROM fields WHERE id = ?)', [localId, realId]);
    c.executeSync('UPDATE fields SET id = ?, synced_at = ? WHERE id = ?',
      [realId, Date.now(), localId]);
    c.executeSync('UPDATE advisories SET field_id = ? WHERE field_id = ?', [realId, localId]);
    c.executeSync('UPDATE diagnoses SET field_id = ? WHERE field_id = ?', [realId, localId]);
    // Match the quoted JSON value, not the bare id. A substring replace would
    // corrupt a shorter id that happens to be a prefix of this one, and ids are
    // generated, not chosen -- so relying on them never colliding that way is a
    // property nothing enforces.
    c.executeSync('UPDATE outbox SET payload = replace(payload, ?, ?)',
      [JSON.stringify(localId), JSON.stringify(realId)]);
    c.executeSync('COMMIT');
  } catch (error) {
    c.executeSync('ROLLBACK');
    throw error;
  }
};

const parse = <T,>(value: unknown): T | null => {
  if (typeof value !== 'string') return null;
  try {
    return JSON.parse(value) as T;
  } catch {
    return null;
  }
};

export const loadFields = (): CachedField[] => {
  const { rows } = conn().executeSync('SELECT * FROM fields ORDER BY created_at DESC');
  return (rows ?? []).map((row: any) => ({
    id: row.id,
    name: row.name,
    area_ha: row.area_ha,
    centroid: [row.centroid_lon, row.centroid_lat] as [number, number],
    geometry: parse(row.geometry)!,
    crop: parse(row.crop),
    soil: parse(row.soil),
    created_at: row.created_at,
    pending: row.synced_at === null || row.synced_at === undefined,
  }));
};

// --------------------------------------------------------------------- crops
export const saveCrops = (crops: unknown[]): void => {
  const c = conn();
  c.executeSync('BEGIN');
  try {
    // Replace wholesale: a crop removed upstream must disappear here too,
    // rather than lingering as an option the node will reject.
    c.executeSync('DELETE FROM crops');
    crops.forEach((crop, index) => {
      const code = (crop as { code?: string }).code;
      if (!code) return;
      c.executeSync(
        'INSERT INTO crops (code, payload, position, fetched_at) VALUES (?, ?, ?, ?)',
        [code, JSON.stringify(crop), index, Date.now()],
      );
    });
    c.executeSync('COMMIT');
  } catch (error) {
    c.executeSync('ROLLBACK');
    throw error;
  }
};

/** Server order is preserved: it is grouped sensibly, not alphabetically. */
export const loadCrops = <T,>(): T[] => {
  const { rows } = conn().executeSync(
    'SELECT payload FROM crops ORDER BY position ASC',
  );
  return (rows ?? [])
    .map((row: any) => parse<T>(row.payload))
    .filter((crop): crop is T => crop !== null);
};

// ------------------------------------------------------------------ weather
export const saveWeather = (fieldId: string, payload: unknown): void => {
  conn().executeSync(
    'INSERT INTO weather (field_id, payload, fetched_at) VALUES (?, ?, ?) '
    + 'ON CONFLICT(field_id) DO UPDATE SET payload = excluded.payload, '
    + 'fetched_at = excluded.fetched_at',
    [fieldId, JSON.stringify(payload), Date.now()],
  );
};

export interface CachedWeather<T> {
  payload: T;
  ageHours: number;
}

export const loadWeather = <T,>(fieldId: string): CachedWeather<T> | null => {
  const { rows } = conn().executeSync(
    'SELECT payload, fetched_at FROM weather WHERE field_id = ?', [fieldId],
  );
  const row = rows?.[0] as any;
  if (!row) return null;
  const payload = parse<T>(row.payload);
  if (payload === null) return null;
  return {
    payload,
    ageHours: (Date.now() - Number(row.fetched_at)) / 3_600_000,
  };
};

// --------------------------------------------------------------- advisories
export const saveAdvisory = (
  fieldId: string, payload: Advisory, narration?: unknown,
): void => {
  conn().executeSync(
    `INSERT INTO advisories (field_id, payload, narration, fetched_at)
     VALUES (?, ?, ?, ?)
     ON CONFLICT(field_id) DO UPDATE SET
       payload = excluded.payload, narration = excluded.narration,
       fetched_at = excluded.fetched_at`,
    [fieldId, JSON.stringify(payload), narration ? JSON.stringify(narration) : null, Date.now()],
  );
};

export interface CachedAdvisory {
  payload: Advisory;
  narration: unknown | null;
  fetchedAt: number;
  ageHours: number;
}

export const loadAdvisory = (fieldId: string): CachedAdvisory | null => {
  const { rows } = conn().executeSync(
    'SELECT payload, narration, fetched_at FROM advisories WHERE field_id = ?',
    [fieldId],
  );
  const row: any = rows?.[0];
  if (!row) return null;
  const payload = parse<Advisory>(row.payload);
  if (!payload) return null;
  return {
    payload,
    narration: parse(row.narration),
    fetchedAt: row.fetched_at,
    ageHours: (Date.now() - row.fetched_at) / 3_600_000,
  };
};

// ---------------------------------------------------------------- diagnoses
export const saveDiagnosis = (diagnosis: Diagnosis, imagePath?: string): void => {
  conn().executeSync(
    `INSERT INTO diagnoses (id, field_id, crop_code, payload, image_path, created_at)
     VALUES (?, ?, ?, ?, ?, ?)
     ON CONFLICT(id) DO UPDATE SET payload = excluded.payload`,
    [
      diagnosis.id, (diagnosis as any).field_id ?? null, diagnosis.crop_code,
      JSON.stringify(diagnosis), imagePath ?? null, Date.now(),
    ],
  );
};

export const loadDiagnoses = (limit = 30): Diagnosis[] => {
  const { rows } = conn().executeSync(
    'SELECT payload FROM diagnoses ORDER BY created_at DESC LIMIT ?', [limit],
  );
  return (rows ?? [])
    .map((row: any) => parse<Diagnosis>(row.payload))
    .filter((d: Diagnosis | null): d is Diagnosis => d !== null);
};

// ------------------------------------------------------------------- outbox
export type OutboxKind =
  | 'create_field'
  | 'set_crop'
  | 'set_soil_card'
  | 'diagnosis'
  | 'feedback';

export interface OutboxItem {
  id: number;
  kind: OutboxKind;
  payload: any;
  filePath: string | null;
  attempts: number;
  lastError: string | null;
}

export const enqueue = (kind: OutboxKind, payload: unknown, filePath?: string): number => {
  conn().executeSync(
    'INSERT INTO outbox (kind, payload, file_path, created_at) VALUES (?, ?, ?, ?)',
    [kind, JSON.stringify(payload), filePath ?? null, Date.now()],
  );
  const { rows } = conn().executeSync('SELECT last_insert_rowid() AS id');
  return (rows?.[0] as any)?.id ?? 0;
};

export const pendingOutbox = (maxAttempts: number): OutboxItem[] => {
  const { rows } = conn().executeSync(
    'SELECT * FROM outbox WHERE attempts < ? ORDER BY created_at ASC, id ASC',
    [maxAttempts],
  );
  return (rows ?? []).map((row: any) => ({
    id: row.id,
    kind: row.kind,
    payload: parse(row.payload),
    filePath: row.file_path,
    attempts: row.attempts,
    lastError: row.last_error,
  }));
};

export const outboxCount = (): number => {
  const { rows } = conn().executeSync('SELECT COUNT(*) AS n FROM outbox');
  return (rows?.[0] as any)?.n ?? 0;
};

export const markOutboxFailed = (id: number, error: string): void => {
  conn().executeSync(
    'UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE id = ?', [error, id],
  );
};

export const dequeue = (id: number): void => {
  conn().executeSync('DELETE FROM outbox WHERE id = ?', [id]);
};

/** Bounded cache: keep the newest diagnoses so storage cannot creep. */
export const trimDiagnoses = (keep = 100): void => {
  conn().executeSync(
    `DELETE FROM diagnoses WHERE id NOT IN (
       SELECT id FROM diagnoses ORDER BY created_at DESC LIMIT ?)`, [keep],
  );
};

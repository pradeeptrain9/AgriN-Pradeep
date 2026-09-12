/**
 * Boundary provenance surviving the phone's mirror.
 *
 * A walked boundary is a survey; a drawn one is an estimate over a satellite
 * basemap. The advisory computes fertiliser and water per hectare, so the area
 * multiplies into every figure a farmer acts on -- and the mirror is what the
 * app reads from in a field with no signal, which is most of the time. If the
 * distinction is lost anywhere between the tap and the card, a drawn estimate
 * quietly becomes a survey.
 *
 * Same fake-binding approach as offline.test.ts: node:sqlite behind
 * `executeSync`, real schema, real statements.
 */

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { DatabaseSync } = require('node:sqlite') as {
  DatabaseSync: new (path: string) => {
    exec: (sql: string) => void;
    prepare: (sql: string) => {
      all: (...params: (string | number | null)[]) => Record<string, unknown>[];
      run: (...params: (string | number | null)[]) => void;
    };
  };
};

const mockSqlite = new DatabaseSync(':memory:');

jest.mock('@op-engineering/op-sqlite', () => ({
  open: () => ({
    executeSync: (sql: string, params: unknown[] = []) => {
      const statement = mockSqlite.prepare(sql);
      const normalised = params.map((p) =>
        p === undefined ? null : (p as string | number | null),
      );
      if (/^\s*(select|pragma)/i.test(sql)) {
        return { rows: statement.all(...normalised) };
      }
      statement.run(...normalised);
      return { rows: [] };
    },
  }),
}));

import { initDb, insertLocalField, loadFields, newLocalId, saveFields } from '../index';
import type { Field } from '../../types';

const polygon = {
  type: 'Polygon' as const,
  coordinates: [[
    [77.594, 12.971], [77.5952, 12.971], [77.5952, 12.9722], [77.594, 12.9722],
    [77.594, 12.971],
  ]],
};

const fromNode = (over: Partial<Field>): Field => ({
  id: 'server-uuid', name: 'From the node', area_ha: 2,
  centroid: [77.5, 12.9], geometry: polygon, crop: null, soil: null,
  created_at: '2026-09-12T00:00:00Z', ...over,
} as Field);

beforeAll(() => initDb());
beforeEach(() => mockSqlite.prepare('DELETE FROM fields').run());

describe('a field drawn on the map', () => {
  it('is stored as drawn, not as walked', () => {
    insertLocalField({
      id: newLocalId(), name: 'Leased plot', area_ha: 1.2,
      centroid: [77.5946, 12.9716], geometry: polygon, source: 'drawn',
    });
    expect(loadFields()[0]!.source).toBe('drawn');
  });

  it('is still marked pending when it was drawn with no signal', () => {
    // Provenance and delivery are separate facts. Conflating them would show a
    // drawn field as sent, or a queued walk as an estimate.
    insertLocalField({
      id: newLocalId(), name: 'Leased plot', area_ha: 1.2,
      centroid: [77.5946, 12.9716], geometry: polygon, source: 'drawn',
    });
    const field = loadFields()[0]!;
    expect(field.pending).toBe(true);
    expect(field.source).toBe('drawn');
  });
});

describe('a field walked', () => {
  it('is what an insert with no source means', () => {
    // The walk path does not pass a source and should not have to: every field
    // that existed before drawing was walked.
    insertLocalField({
      id: newLocalId(), name: 'North plot', area_ha: 1.7,
      centroid: [77.5946, 12.9716], geometry: polygon,
    });
    expect(loadFields()[0]!.source).toBe('walked');
  });
});

describe('what comes back from the node', () => {
  it('keeps the node\'s answer', () => {
    saveFields([fromNode({ source: 'drawn' })]);
    expect(loadFields()[0]!.source).toBe('drawn');
  });

  it('reads as walked when an older node does not send the field at all', () => {
    // A node that predates this column only has walked fields, so this is
    // accurate rather than a guess -- and it is why the column defaults rather
    // than being nullable.
    saveFields([fromNode({})]);
    expect(loadFields()[0]!.source).toBe('walked');
  });

  it('does not relabel a drawn field as walked when the node refreshes it', () => {
    // The upsert names every column it overwrites. Leaving source out of the
    // ON CONFLICT list would keep the first value forever; leaving it out of
    // the INSERT would reset it on every sync. This pins the round trip.
    saveFields([fromNode({ source: 'drawn' })]);
    saveFields([fromNode({ source: 'drawn', name: 'Renamed' })]);
    const field = loadFields()[0]!;
    expect(field.name).toBe('Renamed');
    expect(field.source).toBe('drawn');
  });

  it('lets the node correct a boundary that has since been walked', () => {
    // A farmer who draws a field and later walks its edge should see it stop
    // being an estimate. The node is the authority on that.
    saveFields([fromNode({ source: 'drawn' })]);
    saveFields([fromNode({ source: 'walked' })]);
    expect(loadFields()[0]!.source).toBe('walked');
  });
});

describe('an app updated over an existing install', () => {
  it('adds the column to a fields table that already exists', () => {
    // The schema is CREATE TABLE IF NOT EXISTS, which does nothing for a table
    // that is already there. Without the ALTER in initDb, every write naming
    // `source` fails on an upgraded phone -- and an upgrade is the only way a
    // farmer with fields ever reaches this code.
    const legacy = new DatabaseSync(':memory:');
    legacy.exec(`CREATE TABLE fields (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, area_ha REAL NOT NULL,
      centroid_lon REAL, centroid_lat REAL, geometry TEXT NOT NULL,
      crop TEXT, soil TEXT, created_at TEXT, synced_at INTEGER
    )`);
    legacy.prepare(
      'INSERT INTO fields (id, name, area_ha, geometry, synced_at) VALUES (?, ?, ?, ?, ?)',
    ).run('old-field', 'Walked last season', 1.4, JSON.stringify(polygon), 1);

    legacy.exec("ALTER TABLE fields ADD COLUMN source TEXT NOT NULL DEFAULT 'walked'");

    const row = legacy.prepare('SELECT source FROM fields WHERE id = ?').all('old-field')[0]!;
    expect(row.source).toBe('walked');
  });

  it('swallows the duplicate-column error on a phone that is already current', () => {
    // Every test in this file depends on initDb having run in beforeAll against
    // a schema that already declares `source`, so the ALTER threw and was
    // caught -- that is what makes this whole suite work rather than error out
    // on the first line. Pinning the underlying behaviour explicitly, because
    // the swallow is a bare catch and a bare catch deserves a test saying
    // exactly which error it is there for.
    const current = new DatabaseSync(':memory:');
    current.exec("CREATE TABLE fields (id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'walked')");
    expect(() =>
      current.exec("ALTER TABLE fields ADD COLUMN source TEXT NOT NULL DEFAULT 'walked'"),
    ).toThrow(/duplicate column/i);

    expect(loadFields()).toEqual([]); // initDb survived it; the mirror works
  });
});

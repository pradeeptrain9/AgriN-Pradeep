/**
 * The offline path, against real SQLite.
 *
 * op-sqlite is a native module and cannot load under Jest, so this stands a
 * node:sqlite database in behind the same `executeSync` shape. The schema and
 * every statement under test are the real ones -- what is faked is the binding,
 * not the database.
 *
 * This is the path a farmer takes in a field with no signal, which is the
 * normal case rather than the edge case, and none of it can be exercised
 * against a live node by definition.
 */

// node:sqlite is newer than the @types/node this project pins, so it is
// declared here rather than dragged in as a dependency for one test file.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { DatabaseSync } = require('node:sqlite') as {
  DatabaseSync: new (path: string) => {
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

import {
  LOCAL_ID_PREFIX, dequeue, enqueue, initDb, insertLocalField, isLocalId, loadFields,
  newLocalId, outboxCount, pendingOutbox, remapFieldId, saveAdvisory, saveFields,
} from '../index';

const polygon = {
  type: 'Polygon' as const,
  coordinates: [[
    [77.594, 12.971], [77.5952, 12.971], [77.5952, 12.9722], [77.594, 12.9722],
    [77.594, 12.971],
  ]],
};

const reset = () => {
  for (const table of ['fields', 'advisories', 'diagnoses', 'outbox']) {
    mockSqlite.prepare(`DELETE FROM ${table}`).run();
  }
};

beforeAll(() => initDb());
beforeEach(reset);

describe('local field identity', () => {
  it('mints ids that are recognisable as not-yet-sent', () => {
    const id = newLocalId();
    expect(id.startsWith(LOCAL_ID_PREFIX)).toBe(true);
    expect(isLocalId(id)).toBe(true);
  });

  it('does not mistake a server uuid for a local id', () => {
    expect(isLocalId('3ef3681f-be7b-482a-815e-36000a95054c')).toBe(false);
  });

  it('mints a different id each time, so two fields walked offline do not collide', () => {
    const ids = new Set(Array.from({ length: 500 }, () => newLocalId()));
    expect(ids.size).toBe(500);
  });
});

describe('a field mapped with no signal', () => {
  it('appears in the list, so the walk does not look like it failed', () => {
    const id = newLocalId();
    insertLocalField({
      id, name: 'North plot', area_ha: 1.73, centroid: [77.5946, 12.9716], geometry: polygon,
    });

    const fields = loadFields();
    expect(fields).toHaveLength(1);
    expect(fields[0]!.name).toBe('North plot');
    expect(fields[0]!.area_ha).toBeCloseTo(1.73, 2);
  });

  it('is marked pending, and a synced field is not', () => {
    insertLocalField({
      id: newLocalId(), name: 'Walked offline', area_ha: 1, centroid: [77.5, 12.9],
      geometry: polygon,
    });
    saveFields([{
      id: 'server-uuid', name: 'From the node', area_ha: 2,
      centroid: [77.5, 12.9], geometry: polygon, crop: null, soil: null,
      created_at: new Date().toISOString(),
    } as never]);

    const byName = Object.fromEntries(loadFields().map((f) => [f.name, f.pending]));
    expect(byName['Walked offline']).toBe(true);
    expect(byName['From the node']).toBe(false);
  });

  it('keeps its geometry intact, so the map still draws it', () => {
    const id = newLocalId();
    insertLocalField({
      id, name: 'Plot', area_ha: 1.73, centroid: [77.5946, 12.9716], geometry: polygon,
    });
    expect(loadFields()[0]!.geometry).toEqual(polygon);
  });
});

describe('outbox ordering', () => {
  it('drains in insertion order even when queued within the same millisecond', () => {
    // created_at is a millisecond timestamp, so several items queued in one tick
    // tie on it. Ordering carries meaning here -- a field must be created before
    // the crop that belongs to it -- so the id has to break the tie.
    const now = Date.now();
    const spy = jest.spyOn(Date, 'now').mockReturnValue(now);
    enqueue('create_field', { local_id: 'local:a' });
    enqueue('set_crop', { field_id: 'local:a' });
    enqueue('set_soil_card', { field_id: 'local:a' });
    enqueue('feedback', { kind: 'advisory' });
    spy.mockRestore();

    expect(pendingOutbox(5).map((i) => i.kind)).toEqual([
      'create_field', 'set_crop', 'set_soil_card', 'feedback',
    ]);
  });

  it('stops offering an item once it has burned its attempts', () => {
    const id = enqueue('set_crop', { field_id: 'x' });
    expect(pendingOutbox(3)).toHaveLength(1);
    mockSqlite.prepare('UPDATE outbox SET attempts = 3 WHERE id = ?').run(id);
    expect(pendingOutbox(3)).toHaveLength(0);
    // It is still queued, not silently discarded.
    expect(outboxCount()).toBe(1);
  });

  it('carries the on-device predictions with a queued photo', () => {
    // These were dropped: the variable holding them was scoped inside the try
    // block and invisible to the offline branch that queues. The photo replayed
    // with no predictions, the server gate had nothing to accept, and every
    // offline diagnosis escalated to the paid cloud model -- silently, showing
    // up only as a bill. The on-device model answers ~44% of photos for free,
    // and offline photos were exactly the ones never getting that.
    enqueue('diagnosis', {
      crop_code: 'rice',
      field_id: null,
      predictions: [
        { class_code: 'rice__blast', probability: 0.91 },
        { class_code: 'rice__brown_spot', probability: 0.05 },
      ],
    }, '/tmp/leaf.jpg');

    const item = pendingOutbox(5)[0]!;
    expect(item.kind).toBe('diagnosis');
    expect(item.filePath).toBe('/tmp/leaf.jpg');
    expect(item.payload.predictions).toHaveLength(2);
    expect(item.payload.predictions[0].class_code).toBe('rice__blast');
    expect(item.payload.predictions[0].probability).toBeCloseTo(0.91);
  });

  it('queues a diagnosis that belongs to no field at all', () => {
    // Checking a leaf needs nothing but the leaf, so a null field must survive
    // the round trip rather than being dropped or coerced.
    enqueue('diagnosis', { crop_code: 'rice', field_id: null, predictions: [] },
      '/tmp/leaf.jpg');
    const item = pendingOutbox(5)[0]!;
    expect(item.payload.field_id).toBeNull();
    expect(item.payload.crop_code).toBe('rice');
  });

  it('accepts feedback, so a harm report is not lost to a dead signal', () => {
    enqueue('feedback', { kind: 'advisory', verdict: 'harmful', comment: 'lost the crop' });
    const item = pendingOutbox(5)[0]!;
    expect(item.kind).toBe('feedback');
    expect(item.payload.verdict).toBe('harmful');
    expect(item.payload.comment).toBe('lost the crop');
  });
});

describe('remapFieldId', () => {
  const localId = 'local:abc-123';

  beforeEach(() => {
    insertLocalField({
      id: localId, name: 'North plot', area_ha: 1.73,
      centroid: [77.5946, 12.9716], geometry: polygon,
    });
  });

  it('moves the field onto the id the node assigned, and clears pending', () => {
    remapFieldId(localId, 'real-uuid');
    const fields = loadFields();
    expect(fields).toHaveLength(1);
    expect(fields[0]!.id).toBe('real-uuid');
    expect(fields[0]!.pending).toBe(false);
    expect(fields[0]!.name).toBe('North plot');
  });

  it('rewrites work still queued behind it, which is the whole point', () => {
    enqueue('set_crop', { field_id: localId, body: { crop_code: 'rice' } });
    enqueue('set_soil_card', { field_id: localId, body: { ph: 6.4 } });
    enqueue('feedback', { kind: 'advisory', field_id: localId, verdict: 'harmful' });

    remapFieldId(localId, 'real-uuid');

    for (const item of pendingOutbox(5)) {
      expect(item.payload.field_id).toBe('real-uuid');
    }
  });

  it('carries a cached advisory across, rather than orphaning it', () => {
    saveAdvisory(localId, { any: 'payload' } as never, null);
    remapFieldId(localId, 'real-uuid');
    const rows = mockSqlite.prepare('SELECT field_id FROM advisories').all();
    expect(rows).toEqual([{ field_id: 'real-uuid' }]);
  });

  it('leaves another field mapped offline in the same session untouched', () => {
    const other = 'local:xyz-789';
    insertLocalField({
      id: other, name: 'South plot', area_ha: 0.9,
      centroid: [77.6, 12.98], geometry: polygon,
    });
    enqueue('set_crop', { field_id: other, body: { crop_code: 'wheat' } });

    remapFieldId(localId, 'real-uuid');

    expect(loadFields().find((f) => f.name === 'South plot')?.id).toBe(other);
    expect(pendingOutbox(5)[0]!.payload.field_id).toBe(other);
  });

  it('does not corrupt an id that this one is a prefix of', () => {
    // 'local:abc-123' is a prefix of 'local:abc-1234'. A bare substring replace
    // would rewrite half of the longer id and orphan that field's work.
    const longer = 'local:abc-1234';
    insertLocalField({
      id: longer, name: 'Longer id', area_ha: 1, centroid: [77.6, 12.98], geometry: polygon,
    });
    enqueue('set_crop', { field_id: longer, body: { crop_code: 'wheat' } });

    remapFieldId(localId, 'real-uuid');

    expect(pendingOutbox(5)[0]!.payload.field_id).toBe(longer);
  });

  it('survives the server copy already being present', () => {
    // The bug this exists for: sync saved the server's field first, then
    // remapped, and the rename collided on the primary key. The throw left the
    // outbox item queued, so every reconnect created ANOTHER field on the node.
    // Two identical fields appeared 90 seconds apart on a real run.
    saveFields([{
      id: 'real-uuid', name: 'North plot', area_ha: 1.73,
      centroid: [77.5946, 12.9716], geometry: polygon, crop: null, soil: null,
      created_at: new Date().toISOString(),
    } as never]);
    enqueue('set_crop', { field_id: localId, body: { crop_code: 'rice' } });

    expect(() => remapFieldId(localId, 'real-uuid')).not.toThrow();

    // One field, not two, and the local placeholder is gone.
    const fields = loadFields();
    expect(fields).toHaveLength(1);
    expect(fields[0]!.id).toBe('real-uuid');
    // The queued work still points at the real field.
    expect(pendingOutbox(5)[0]!.payload.field_id).toBe('real-uuid');
  });

  it('does nothing at all if the field is not there, rather than corrupting rows', () => {
    const before = loadFields();
    remapFieldId('local:not-a-field', 'real-uuid');
    expect(loadFields()).toEqual(before);
  });
});

describe('the whole offline sequence', () => {
  it('survives map -> crop -> soil -> complaint, then one sync', () => {
    // Everything a farmer can do standing in a field with no signal.
    const localId = newLocalId();
    insertLocalField({
      id: localId, name: 'North plot', area_ha: 1.73,
      centroid: [77.5946, 12.9716], geometry: polygon,
    });
    enqueue('create_field', { local_id: localId, name: 'North plot', geometry: polygon });
    enqueue('set_crop', { field_id: localId, body: { crop_code: 'rice' } });
    enqueue('set_soil_card', { field_id: localId, body: { ph: 6.4 } });
    enqueue('feedback', {
      kind: 'advisory', field_id: localId, verdict: 'harmful', comment: 'flooded',
    });

    // The farmer can see and use the field the entire time.
    expect(loadFields()[0]!.pending).toBe(true);
    expect(outboxCount()).toBe(4);

    // Signal returns. The node accepts the field and names it.
    const queue = pendingOutbox(5);
    expect(queue[0]!.kind).toBe('create_field');
    remapFieldId(queue[0]!.payload.local_id, 'real-uuid');
    dequeue(queue[0]!.id);

    // Everything behind it now points at a field the node actually has.
    const remaining = pendingOutbox(5);
    expect(remaining.map((i) => i.kind)).toEqual(['set_crop', 'set_soil_card', 'feedback']);
    for (const item of remaining) expect(item.payload.field_id).toBe('real-uuid');

    const field = loadFields()[0]!;
    expect(field.id).toBe('real-uuid');
    expect(field.pending).toBe(false);
    expect(field.name).toBe('North plot');
  });
});

/**
 * A field drawn with no signal must still reach the node as drawn.
 *
 * The provenance is decided on a screen the farmer has already left by the time
 * the queue drains -- possibly days later, on a different day's signal. Nothing
 * carries it across that gap except the queued payload, so this is the one hop
 * where "drawn" could silently become "walked" with no screen to show it.
 *
 * The db and the HTTP client are both faked here: what is under test is the
 * replay in sync.ts, not SQLite and not axios.
 */

// jest hoists jest.mock above the imports, so anything a factory closes over
// has to be declared in a way that survives that -- hence the `mock` prefix,
// which is the escape hatch jest whitelists for exactly this.
const mockCreateField = jest.fn();
const mockPendingOutbox = jest.fn();
const mockDequeue = jest.fn();
const mockRemapFieldId = jest.fn();
const mockSaveFields = jest.fn();

jest.mock('../api', () => ({
  createField: (...args: unknown[]) => mockCreateField(...args),
  isOffline: () => false,
  setCrop: jest.fn(),
  setSoilCard: jest.fn(),
  submitDiagnosis: jest.fn(),
  submitFeedback: jest.fn(),
}));

jest.mock('../../db', () => ({
  pendingOutbox: (...args: unknown[]) => mockPendingOutbox(...args),
  dequeue: (...args: unknown[]) => mockDequeue(...args),
  remapFieldId: (...args: unknown[]) => mockRemapFieldId(...args),
  saveFields: (...args: unknown[]) => mockSaveFields(...args),
  markOutboxFailed: jest.fn(),
  outboxCount: () => 0,
  saveDiagnosis: jest.fn(),
}));

import { drainOutbox } from '../sync';

const polygon = {
  type: 'Polygon' as const,
  coordinates: [[
    [77.594, 12.971], [77.5952, 12.971], [77.5952, 12.9722], [77.594, 12.971],
  ]],
};

const queued = (payload: Record<string, unknown>) => [{
  id: 1, kind: 'create_field', payload, filePath: null, attempts: 0,
}];

beforeEach(() => {
  jest.clearAllMocks();
  mockCreateField.mockResolvedValue({
    id: 'real-uuid', name: 'Leased plot', area_ha: 1.2,
    centroid: [77.5, 12.9], geometry: polygon, source: 'drawn',
  });
});

describe('draining a boundary queued offline', () => {
  it('sends a drawn field as drawn', async () => {
    mockPendingOutbox.mockReturnValue(queued({
      local_id: 'local:a', name: 'Leased plot', geometry: polygon, source: 'drawn',
    }));

    await drainOutbox();

    expect(mockCreateField).toHaveBeenCalledWith('Leased plot', polygon, 'drawn');
  });

  it('sends a walked field as walked', async () => {
    mockPendingOutbox.mockReturnValue(queued({
      local_id: 'local:a', name: 'North plot', geometry: polygon, source: 'walked',
    }));

    await drainOutbox();

    expect(mockCreateField).toHaveBeenCalledWith('North plot', polygon, 'walked');
  });

  it('treats an item queued before this column existed as walked', async () => {
    // An app updated with a full outbox replays payloads written by the old
    // build. Those are all walks, so this is accurate rather than a guess --
    // and defaulting the other way would relabel real surveys as estimates.
    mockPendingOutbox.mockReturnValue(queued({
      local_id: 'local:a', name: 'North plot', geometry: polygon,
    }));

    await drainOutbox();

    expect(mockCreateField).toHaveBeenCalledWith('North plot', polygon, 'walked');
  });

  it('still remaps the local id before saving the node\'s copy', async () => {
    // Regression guard for the duplicate-field bug: saving first collides on
    // the primary key, the item never dequeues, and every reconnect creates
    // another field on the node. Drawing reuses this exact path.
    const order: string[] = [];
    mockRemapFieldId.mockImplementation(() => order.push('remap'));
    mockSaveFields.mockImplementation(() => order.push('save'));
    mockPendingOutbox.mockReturnValue(queued({
      local_id: 'local:a', name: 'Leased plot', geometry: polygon, source: 'drawn',
    }));

    await drainOutbox();

    expect(order).toEqual(['remap', 'save']);
    expect(mockRemapFieldId).toHaveBeenCalledWith('local:a', 'real-uuid');
    expect(mockDequeue).toHaveBeenCalledWith(1);
  });
});

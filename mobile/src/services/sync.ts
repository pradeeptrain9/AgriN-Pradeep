/**
 * Outbox drain.
 *
 * Work queued while offline is replayed in insertion order, because ordering
 * carries meaning: a field must exist before the crop cycle that belongs to it.
 * A failed item stops the drain rather than being skipped, for the same reason.
 */

import {
  dequeue, markOutboxFailed, outboxCount, pendingOutbox, remapFieldId, saveDiagnosis,
  saveFields, type OutboxItem,
} from '../db';
import { SYNC } from '../constants/config';
import {
  createField, isOffline, setCrop, setSoilCard, submitDiagnosis, submitFeedback,
} from './api';

export interface SyncResult {
  sent: number;
  failed: number;
  remaining: number;
  offline: boolean;
}

const replay = async (item: OutboxItem): Promise<void> => {
  switch (item.kind) {
    case 'create_field': {
      const field = await createField(item.payload.name, item.payload.geometry);

      // Remap BEFORE saving the server's copy. The other order inserts a row
      // under the new id, and the remap's UPDATE then collides with it on the
      // primary key -- which threw, left the item queued, and created a fresh
      // duplicate field on the node at every reconnect.
      //
      // This also rewrites everything the farmer attached while offline -- crop,
      // soil card, leaf photos, a complaint -- onto the id the node assigned,
      // so the rest of the queue does not replay against a field it has never
      // heard of.
      if (item.payload.local_id) {
        remapFieldId(item.payload.local_id, field.id);
      }
      saveFields([field]);
      return;
    }
    case 'set_crop':
      await setCrop(item.payload.field_id, item.payload.body);
      return;
    case 'set_soil_card':
      await setSoilCard(item.payload.field_id, item.payload.body);
      return;
    case 'feedback':
      await submitFeedback(item.payload);
      return;
    case 'diagnosis': {
      if (!item.filePath) return;
      const diagnosis = await submitDiagnosis({
        cropCode: item.payload.crop_code,
        imageUri: item.filePath,
        fieldId: item.payload.field_id,
        predictions: item.payload.predictions,
      });
      saveDiagnosis(diagnosis, item.filePath);
      return;
    }
    default:
      throw new Error(`unknown outbox kind: ${item.kind}`);
  }
};

export const drainOutbox = async (): Promise<SyncResult> => {
  const items = pendingOutbox(SYNC.maxAttempts);
  let sent = 0;
  let failed = 0;

  for (const item of items) {
    try {
      await replay(item);
      dequeue(item.id);
      sent += 1;
    } catch (error) {
      markOutboxFailed(item.id, String(error));
      failed += 1;
      // Still offline: stop now rather than burning attempts on every item.
      if (isOffline(error)) {
        return { sent, failed, remaining: outboxCount(), offline: true };
      }
      // Ordering matters, so a hard failure halts the queue too.
      break;
    }
  }
  return { sent, failed, remaining: outboxCount(), offline: false };
};

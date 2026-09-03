/**
 * On-device rice disease classifier.
 *
 * The model answers only about 44% of photos; the rest fail the confidence gate
 * and go to the server. That is by design, not a shortfall: the raw model is
 * 78.6% accurate, and the gate turns that into 98.1% accuracy on what a farmer
 * actually sees by declining the ones it would get wrong. Measured on held-out
 * validation, zero healthy leaves were shown a disease label, against 37% in the
 * raw model.
 *
 * Loading is lazy and failure is never fatal. If the asset is missing or the
 * device cannot allocate it, every photo simply routes to the server, which is
 * the same path an unsupported crop takes.
 */

import { decode as decodeJpeg } from 'jpeg-js';
import RNFS from 'react-native-fs';

import { DISEASE_MODEL } from '../constants/config';
import type { DiseasePrediction } from '../types';

const INPUT_SIZE = DISEASE_MODEL.inputSize;

let model: any = null;
let loadAttempted = false;
let labels: string[] = [];

export const isModelLoaded = (): boolean => model !== null;

export const loadModel = async (): Promise<boolean> => {
  if (model) return true;
  if (loadAttempted) return false;
  loadAttempted = true;

  try {
    const { loadTensorflowModel } = require('react-native-fast-tflite');
    // CPU delegate only: the GPU delegate library is excluded from the build
    // because it added 1.25 MB per ABI to speed up an inference that already
    // takes tens of milliseconds.
    model = await loadTensorflowModel(
      require('../assets/models/rice_disease.tflite'),
      'default',
    );
    labels = require('../assets/models/labels.json');
    return true;
  } catch (error) {
    console.warn('on-device model unavailable, using server diagnosis', error);
    model = null;
    return false;
  }
};

const B64_ALPHABET =
  'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

/**
 * Base64 to bytes, written out rather than borrowed.
 *
 * Hermes provides neither `atob` nor Node's `Buffer`, and pulling in a polyfill
 * for one call would add to an APK that already carries an 8 MB map.
 */
const base64ToBytes = (input: string): Uint8Array => {
  const clean = input.replace(/[^A-Za-z0-9+/]/g, '');
  const bytes = new Uint8Array(((clean.length * 3) / 4) | 0);
  let byteIndex = 0;
  let buffer = 0;
  let bits = 0;

  for (let i = 0; i < clean.length; i += 1) {
    buffer = (buffer << 6) | B64_ALPHABET.indexOf(clean[i]!);
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes[byteIndex] = (buffer >> bits) & 0xff;
      byteIndex += 1;
    }
  }
  return bytes.subarray(0, byteIndex);
};

/**
 * Decode a JPEG and resize it to the model's input, matching training exactly.
 *
 * Two details that cost accuracy if got wrong, both measured:
 *
 * 1. BILINEAR, not nearest-neighbour. Nearest-neighbour resampling dropped
 *    confidence on a held-out leaf from 0.873 to 0.715 -- 16 points, enough to
 *    push a correct prediction under the 0.70 gate and send it to the server
 *    for no reason.
 *
 * 2. STRETCH, not letterbox. Training used tf.image.resize to a square 224,
 *    which stretches. Letterboxing onto grey padding is a different input
 *    distribution from the one the weights were fitted to, so it must not be
 *    "improved" here.
 *
 * The model was trained on 0-255 inputs; MobileNetV3's preprocessing layer is
 * inside the graph, so no normalisation belongs here.
 */
export const preprocess = async (imageUri: string): Promise<Float32Array> => {
  const path = imageUri.replace('file://', '');
  const base64 = await RNFS.readFile(path, 'base64');
  const decoded = decodeJpeg(base64ToBytes(base64), {
    useTArray: true, formatAsRGBA: false,
  });

  const { width, height, data } = decoded;
  const channels = data.length / (width * height);
  const output = new Float32Array(INPUT_SIZE * INPUT_SIZE * 3);

  const scaleX = width / INPUT_SIZE;
  const scaleY = height / INPUT_SIZE;

  for (let y = 0; y < INPUT_SIZE; y += 1) {
    // Half-pixel centres, the same convention tf.image.resize uses.
    const sourceY = Math.min(height - 1, Math.max(0, (y + 0.5) * scaleY - 0.5));
    const y0 = Math.floor(sourceY);
    const y1 = Math.min(height - 1, y0 + 1);
    const wy = sourceY - y0;

    for (let x = 0; x < INPUT_SIZE; x += 1) {
      const sourceX = Math.min(width - 1, Math.max(0, (x + 0.5) * scaleX - 0.5));
      const x0 = Math.floor(sourceX);
      const x1 = Math.min(width - 1, x0 + 1);
      const wx = sourceX - x0;

      const topLeft = (y0 * width + x0) * channels;
      const topRight = (y0 * width + x1) * channels;
      const bottomLeft = (y1 * width + x0) * channels;
      const bottomRight = (y1 * width + x1) * channels;
      const destination = (y * INPUT_SIZE + x) * 3;

      for (let c = 0; c < 3; c += 1) {
        const top = (data[topLeft + c] ?? 0) * (1 - wx) + (data[topRight + c] ?? 0) * wx;
        const bottom =
          (data[bottomLeft + c] ?? 0) * (1 - wx) + (data[bottomRight + c] ?? 0) * wx;
        output[destination + c] = top * (1 - wy) + bottom * wy;
      }
    }
  }
  return output;
};

/**
 * Returns the softmax over ALL classes for the crop.
 *
 * Deliberately not truncated to the top few: the server normalises entropy by
 * the crop's class count and treats missing probability mass as uncertainty, so
 * sending a partial distribution would make a confident prediction look
 * uncertain and waste the upload.
 */
export const classify = async (
  imageUri: string, cropCode: string,
): Promise<DiseasePrediction[]> => {
  const ready = await loadModel();
  if (!ready || !model) return [];

  try {
    const input = await preprocess(imageUri);
    const output = await model.run([input]);
    const raw = Array.from(output[0] ?? []) as number[];
    if (raw.length !== labels.length) {
      console.warn(`model returned ${raw.length} outputs for ${labels.length} labels`);
      return [];
    }

    const total = raw.reduce((sum, v) => sum + v, 0);
    const normalised = total > 0 ? raw.map((v) => v / total) : raw;

    return normalised
      .map((probability, index) => ({
        class_code: labels[index] ?? `class_${index}`,
        probability,
      }))
      .filter((p) => p.class_code.startsWith(cropPrefix(cropCode)));
  } catch (error) {
    // A failed inference must not block the photo: the server can still answer.
    console.warn('on-device inference failed', error);
    return [];
  }
};

const cropPrefix = (cropCode: string): string =>
  cropCode.startsWith('wheat') ? 'wheat__' : `${cropCode}__`;

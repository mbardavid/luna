import crypto from 'node:crypto';
import { fromRoot } from '../utils/paths.mjs';
import { readJson, stableStringify, writeJson } from '../utils/fs.mjs';

const STORE_PATH = fromRoot('state', 'idempotency.json');

function loadStore() {
  return readJson(STORE_PATH, { keys: {} });
}

function saveStore(store) {
  writeJson(STORE_PATH, store);
}

export function computeIdempotencyKey(intent, policyVersion) {
  const payload = stableStringify({ policyVersion, intent });
  return crypto.createHash('sha256').update(payload).digest('hex');
}

export function cleanupExpired(ttlDays) {
  const store = loadStore();
  const ttlMs = ttlDays * 24 * 60 * 60 * 1000;
  const now = Date.now();

  for (const [key, value] of Object.entries(store.keys)) {
    if (now - new Date(value.updatedAt).getTime() > ttlMs) {
      delete store.keys[key];
    }
  }

  saveStore(store);
}

export function getRecord(idempotencyKey) {
  const store = loadStore();
  return store.keys[idempotencyKey] ?? null;
}

export function markPending(idempotencyKey, runId) {
  const store = loadStore();
  store.keys[idempotencyKey] = {
    status: 'pending',
    runId,
    updatedAt: new Date().toISOString()
  };
  saveStore(store);
}

export function markConfirmationRequired(idempotencyKey, runId, { riskClassification = null, riskClassificationSource = null } = {}) {
  const store = loadStore();
  store.keys[idempotencyKey] = {
    status: 'confirmation_required',
    runId,
    riskClassification,
    riskClassificationSource,
    updatedAt: new Date().toISOString()
  };
  saveStore(store);
}

export function markSuccess(idempotencyKey, runId, result = {}) {
  const store = loadStore();
  store.keys[idempotencyKey] = {
    status: 'success',
    runId,
    result,
    updatedAt: new Date().toISOString()
  };
  saveStore(store);
}

export function markFailure(idempotencyKey, runId, error = {}) {
  const store = loadStore();
  store.keys[idempotencyKey] = {
    status: 'failure',
    runId,
    error,
    updatedAt: new Date().toISOString()
  };
  saveStore(store);
}

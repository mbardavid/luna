import crypto from 'node:crypto';
import fs from 'node:fs';
import { OperatorError } from '../utils/errors.mjs';
import { fromRoot } from '../utils/paths.mjs';
import { readJson, stableStringify, writeJson } from '../utils/fs.mjs';

const NONCE_STORE_PATH = fromRoot('state', 'a2a-nonce-store.json');
const NONCE_LOCK_PATH = fromRoot('state', 'a2a-nonce.lock');
const DEFAULT_MAX_SKEW_MS = 2 * 60 * 1000;
const DEFAULT_NONCE_TTL_MS = 5 * 60 * 1000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseJsonMap(raw, field) {
  if (!raw) return {};

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(`${field} deve ser objeto JSON`);
    }

    return parsed;
  } catch (error) {
    throw new OperatorError('A2A_CONFIG_INVALID', `${field} inválido`, {
      field,
      message: error.message
    });
  }
}

function normalizeMode(value) {
  const mode = String(value ?? 'permissive').trim().toLowerCase();
  if (!['disabled', 'permissive', 'enforce'].includes(mode)) {
    throw new OperatorError('A2A_CONFIG_INVALID', 'A2A_SECURITY_MODE inválido', {
      mode,
      allowed: ['disabled', 'permissive', 'enforce']
    });
  }

  return mode;
}

function parseBoolean(value, defaultValue = false) {
  if (value == null) return defaultValue;
  return ['1', 'true', 'yes', 'on'].includes(String(value).trim().toLowerCase());
}

function readConfig() {
  const mode = normalizeMode(process.env.A2A_SECURITY_MODE);
  const keys = parseJsonMap(process.env.A2A_HMAC_KEYS_JSON ?? '{}', 'A2A_HMAC_KEYS_JSON');

  return {
    mode,
    keys,
    maxSkewMs: Number(process.env.A2A_MAX_SKEW_MS ?? DEFAULT_MAX_SKEW_MS),
    nonceTtlMs: Number(process.env.A2A_NONCE_TTL_MS ?? DEFAULT_NONCE_TTL_MS),
    allowUnsignedLive: parseBoolean(process.env.A2A_ALLOW_UNSIGNED_LIVE, false),
    lockTimeoutMs: Number(process.env.A2A_LOCK_TIMEOUT_MS ?? 3000),
    lockStaleMs: Number(process.env.A2A_LOCK_STALE_MS ?? 10000)
  };
}

function normalizeAuth(auth) {
  if (!auth || typeof auth !== 'object') return null;

  const normalized = {
    scheme: String(auth.scheme ?? ''),
    keyId: String(auth.keyId ?? ''),
    nonce: String(auth.nonce ?? ''),
    timestamp: String(auth.timestamp ?? ''),
    signature: String(auth.signature ?? '')
  };

  if (!normalized.scheme || !normalized.keyId || !normalized.nonce || !normalized.timestamp || !normalized.signature) {
    throw new OperatorError('A2A_AUTH_INVALID', 'auth incompleto. Campos obrigatórios: scheme,keyId,nonce,timestamp,signature', {
      auth
    });
  }

  if (normalized.scheme !== 'hmac-sha256-v1') {
    throw new OperatorError('A2A_AUTH_SCHEME_UNSUPPORTED', 'scheme de auth não suportado', {
      scheme: normalized.scheme
    });
  }

  return normalized;
}

function parseTimestamp(value, field) {
  const ts = Date.parse(String(value));
  if (!Number.isFinite(ts)) {
    throw new OperatorError('A2A_TIMESTAMP_INVALID', `${field} inválido`, {
      field,
      value
    });
  }
  return ts;
}

function canonicalPayloadForSignature(payload) {
  const clone = JSON.parse(JSON.stringify(payload ?? {}));
  if (clone.auth && typeof clone.auth === 'object') {
    delete clone.auth.signature;
  }

  return clone;
}

function decodeProvidedSignature(signature) {
  const trimmed = String(signature).trim();
  if (!trimmed) return [];

  const candidates = [];

  const noPrefix = trimmed.startsWith('0x') ? trimmed.slice(2) : trimmed;
  if (/^[a-fA-F0-9]+$/.test(noPrefix) && noPrefix.length % 2 === 0) {
    candidates.push(Buffer.from(noPrefix, 'hex'));
  }

  try {
    const base64 = Buffer.from(trimmed, 'base64');
    if (base64.length > 0) {
      candidates.push(base64);
    }
  } catch {
    // ignore
  }

  return candidates;
}

function verifySignature({ payload, secret, signature }) {
  const canonical = canonicalPayloadForSignature(payload);
  const serialized = stableStringify(canonical);

  const expected = crypto.createHmac('sha256', secret).update(serialized).digest();
  const providedCandidates = decodeProvidedSignature(signature);

  const valid = providedCandidates.some(
    (candidate) => candidate.length === expected.length && crypto.timingSafeEqual(candidate, expected)
  );

  return {
    valid,
    expectedHex: expected.toString('hex')
  };
}

function loadNonceStore() {
  return readJson(NONCE_STORE_PATH, {
    entries: {}
  });
}

function saveNonceStore(store) {
  writeJson(NONCE_STORE_PATH, store);
}

function pruneNonceStore(store, ttlMs) {
  const now = Date.now();

  for (const [key, value] of Object.entries(store.entries ?? {})) {
    if (!value || now - Number(value.seenAtMs ?? 0) > ttlMs) {
      delete store.entries[key];
    }
  }
}

async function acquireLock({ timeoutMs, staleMs }) {
  const started = Date.now();

  while (Date.now() - started < timeoutMs) {
    try {
      const fd = fs.openSync(NONCE_LOCK_PATH, 'wx');
      fs.writeFileSync(fd, `${process.pid}\n`, 'utf8');
      return fd;
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;

      try {
        const stat = fs.statSync(NONCE_LOCK_PATH);
        if (Date.now() - stat.mtimeMs > staleMs) {
          fs.unlinkSync(NONCE_LOCK_PATH);
          continue;
        }
      } catch (statError) {
        if (statError.code !== 'ENOENT') throw statError;
      }

      await sleep(25 + Math.floor(Math.random() * 30));
    }
  }

  throw new OperatorError('A2A_NONCE_LOCK_TIMEOUT', 'Timeout ao adquirir lock de nonce A2A');
}

function releaseLock(fd) {
  try {
    fs.closeSync(fd);
  } catch {
    // noop
  }

  try {
    fs.unlinkSync(NONCE_LOCK_PATH);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}

async function registerNonce({ keyId, nonce, timestampMs, config }) {
  const fd = await acquireLock({
    timeoutMs: config.lockTimeoutMs,
    staleMs: config.lockStaleMs
  });

  try {
    const store = loadNonceStore();
    pruneNonceStore(store, config.nonceTtlMs);

    const nonceKey = `${keyId}:${nonce}`;
    const existing = store.entries[nonceKey];

    if (existing) {
      throw new OperatorError('A2A_NONCE_REPLAY', 'Nonce já utilizada dentro da janela anti-replay', {
        keyId,
        nonce,
        firstSeenAt: existing.firstSeenAt
      });
    }

    store.entries[nonceKey] = {
      keyId,
      nonce,
      tsMs: timestampMs,
      firstSeenAt: new Date().toISOString(),
      seenAtMs: Date.now()
    };

    saveNonceStore(store);
  } finally {
    releaseLock(fd);
  }
}

function validateTimestampWindow(authTimestampMs, envelopeTimestampMs, maxSkewMs) {
  const now = Date.now();

  const skew = Math.abs(now - authTimestampMs);
  if (skew > maxSkewMs) {
    throw new OperatorError('A2A_TIMESTAMP_WINDOW_EXCEEDED', 'Timestamp auth fora da janela permitida', {
      now: new Date(now).toISOString(),
      authTimestamp: new Date(authTimestampMs).toISOString(),
      maxSkewMs
    });
  }

  if (envelopeTimestampMs != null) {
    const drift = Math.abs(authTimestampMs - envelopeTimestampMs);
    if (drift > maxSkewMs) {
      throw new OperatorError('A2A_TIMESTAMP_DRIFT_EXCEEDED', 'Drift entre auth.timestamp e envelope.timestamp excedeu janela', {
        authTimestamp: new Date(authTimestampMs).toISOString(),
        envelopeTimestamp: new Date(envelopeTimestampMs).toISOString(),
        maxSkewMs
      });
    }
  }
}

function buildUnsignedResult(config, { dryRun, reason }) {
  if (!dryRun && !config.allowUnsignedLive && config.mode !== 'disabled') {
    throw new OperatorError('A2A_AUTH_REQUIRED', 'Payload execution plane live requer auth assinada', {
      mode: config.mode,
      reason
    });
  }

  return {
    mode: config.mode,
    verified: false,
    reason
  };
}

export async function verifyExecutionPlaneSecurity(payload, { dryRun = false } = {}) {
  const config = readConfig();

  if (config.mode === 'disabled') {
    return {
      mode: config.mode,
      verified: false,
      reason: 'security_disabled'
    };
  }

  const auth = normalizeAuth(payload?.auth ?? null);

  if (!auth) {
    const reason = Object.keys(config.keys).length === 0 ? 'no_auth_and_no_keys_configured' : 'no_auth';
    return buildUnsignedResult(config, { dryRun, reason });
  }

  const secret = config.keys[auth.keyId];

  if (!secret) {
    if (!dryRun || config.mode === 'enforce') {
      throw new OperatorError('A2A_KEY_UNKNOWN', 'keyId não conhecido para validação A2A', {
        keyId: auth.keyId
      });
    }

    return {
      mode: config.mode,
      verified: false,
      reason: 'unknown_key_in_dry_run',
      keyId: auth.keyId
    };
  }

  const authTimestampMs = parseTimestamp(auth.timestamp, 'auth.timestamp');
  const envelopeTimestampMs = payload?.timestamp ? parseTimestamp(payload.timestamp, 'timestamp') : null;

  validateTimestampWindow(authTimestampMs, envelopeTimestampMs, config.maxSkewMs);

  const signature = verifySignature({
    payload,
    secret,
    signature: auth.signature
  });

  if (!signature.valid) {
    throw new OperatorError('A2A_SIGNATURE_INVALID', 'Assinatura do payload execution plane inválida', {
      keyId: auth.keyId
    });
  }

  await registerNonce({
    keyId: auth.keyId,
    nonce: auth.nonce,
    timestampMs: authTimestampMs,
    config
  });

  return {
    mode: config.mode,
    verified: true,
    keyId: auth.keyId,
    nonce: auth.nonce,
    authTimestamp: new Date(authTimestampMs).toISOString(),
    envelopeTimestamp: envelopeTimestampMs != null ? new Date(envelopeTimestampMs).toISOString() : null,
    maxSkewMs: config.maxSkewMs,
    nonceTtlMs: config.nonceTtlMs
  };
}

export function getA2ANonceStorePath() {
  return NONCE_STORE_PATH;
}

import fs from 'node:fs';
import { fromRoot } from '../utils/paths.mjs';
import { readJson, writeJson } from '../utils/fs.mjs';
import { OperatorError } from '../utils/errors.mjs';

const NONCE_STORE_PATH = fromRoot('state', 'hyperliquid-nonce.json');
const NONCE_LOCK_PATH = fromRoot('state', 'hyperliquid-nonce.lock');

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function normalizeSigner(signer) {
  if (!signer || typeof signer !== 'string') {
    throw new OperatorError('HL_NONCE_SIGNER_REQUIRED', 'Signer é obrigatório para coordenação de nonce');
  }

  return signer.toLowerCase();
}

function readNonceStore() {
  return readJson(NONCE_STORE_PATH, { signers: {} });
}

function writeNonceStore(store) {
  writeJson(NONCE_STORE_PATH, store);
}

async function acquireLock({ timeoutMs = 5000, staleMs = 15000 } = {}) {
  const started = Date.now();

  while (Date.now() - started < timeoutMs) {
    try {
      const fd = fs.openSync(NONCE_LOCK_PATH, 'wx');
      fs.writeFileSync(fd, `${process.pid}\n`, 'utf8');
      return fd;
    } catch (error) {
      if (error.code !== 'EEXIST') {
        throw error;
      }

      try {
        const stat = fs.statSync(NONCE_LOCK_PATH);
        if (Date.now() - stat.mtimeMs > staleMs) {
          fs.unlinkSync(NONCE_LOCK_PATH);
          continue;
        }
      } catch (statError) {
        if (statError.code !== 'ENOENT') {
          throw statError;
        }
      }

      await sleep(20 + Math.floor(Math.random() * 40));
    }
  }

  throw new OperatorError(
    'HL_NONCE_LOCK_TIMEOUT',
    'Timeout ao adquirir lock de nonce Hyperliquid',
    { timeoutMs }
  );
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
    if (error.code !== 'ENOENT') {
      throw error;
    }
  }
}

export class NonceCoordinator {
  constructor({
    timeoutMs = Number(process.env.HYPERLIQUID_NONCE_LOCK_TIMEOUT_MS ?? 5000),
    staleMs = Number(process.env.HYPERLIQUID_NONCE_LOCK_STALE_MS ?? 15000)
  } = {}) {
    this.timeoutMs = timeoutMs;
    this.staleMs = staleMs;
  }

  async nextNonce({ signer, floor = null } = {}) {
    const normalizedSigner = normalizeSigner(signer);
    const lockFd = await acquireLock({ timeoutMs: this.timeoutMs, staleMs: this.staleMs });

    try {
      const store = readNonceStore();
      const now = Date.now();
      const floorNonce = floor == null ? now : Number(floor);

      if (!Number.isFinite(floorNonce)) {
        throw new OperatorError('HL_NONCE_FLOOR_INVALID', 'Floor de nonce inválido', { floor });
      }

      const signerState = store.signers[normalizedSigner] ?? {};
      const lastNonce = Number(signerState.lastNonce ?? 0);
      const nonce = Math.max(now, floorNonce, lastNonce + 1);

      store.signers[normalizedSigner] = {
        lastNonce: nonce,
        updatedAt: new Date().toISOString()
      };

      writeNonceStore(store);

      return nonce;
    } finally {
      releaseLock(lockFd);
    }
  }
}

export function getNonceStorePath() {
  return NONCE_STORE_PATH;
}

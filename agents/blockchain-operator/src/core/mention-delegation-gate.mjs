import fs from 'node:fs';
import { fromRoot } from '../utils/paths.mjs';
import { readJson, writeJson } from '../utils/fs.mjs';
import { OperatorError } from '../utils/errors.mjs';

const DEDUPE_STORE_PATH = fromRoot('state', 'mention-delegation-dedupe.json');
const DEDUPE_LOCK_PATH = fromRoot('state', 'mention-delegation-dedupe.lock');

const DEFAULT_LOCK_TIMEOUT_MS = 3000;
const DEFAULT_LOCK_STALE_MS = 10000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readStore() {
  return readJson(DEDUPE_STORE_PATH, { entries: {} });
}

function writeStore(store) {
  writeJson(DEDUPE_STORE_PATH, store);
}

function pruneExpired(store, nowMs) {
  for (const [key, value] of Object.entries(store.entries ?? {})) {
    const expiresAtMs = Number(value?.expiresAtMs ?? 0);
    if (!Number.isFinite(expiresAtMs) || expiresAtMs <= nowMs) {
      delete store.entries[key];
    }
  }
}

async function acquireLock({ timeoutMs = DEFAULT_LOCK_TIMEOUT_MS, staleMs = DEFAULT_LOCK_STALE_MS } = {}) {
  const started = Date.now();

  while (Date.now() - started < timeoutMs) {
    try {
      const fd = fs.openSync(DEDUPE_LOCK_PATH, 'wx');
      fs.writeFileSync(fd, `${process.pid}\n`, 'utf8');
      return fd;
    } catch (error) {
      if (error.code !== 'EEXIST') {
        throw error;
      }

      try {
        const stat = fs.statSync(DEDUPE_LOCK_PATH);
        if (Date.now() - stat.mtimeMs > staleMs) {
          fs.unlinkSync(DEDUPE_LOCK_PATH);
          continue;
        }
      } catch (statError) {
        if (statError.code !== 'ENOENT') {
          throw statError;
        }
      }

      await sleep(25 + Math.floor(Math.random() * 35));
    }
  }

  throw new OperatorError(
    'EXECUTION_MENTION_DELEGATION_LOCK_TIMEOUT',
    'Timeout ao adquirir lock de dedupe para mention delegation.',
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
    fs.unlinkSync(DEDUPE_LOCK_PATH);
  } catch (error) {
    if (error.code !== 'ENOENT') {
      throw error;
    }
  }
}

export async function registerMentionDelegationDedupe(
  mentionDelegation,
  {
    nowMs = Date.now(),
    lockTimeoutMs = DEFAULT_LOCK_TIMEOUT_MS,
    lockStaleMs = DEFAULT_LOCK_STALE_MS
  } = {}
) {
  if (!mentionDelegation) {
    return null;
  }

  const expiresAtMs = Date.parse(mentionDelegation.expiresAt);
  if (!Number.isFinite(expiresAtMs)) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_INVALID',
      'expiresAt inválido no contexto de mention delegation.',
      { mentionDelegation }
    );
  }

  if (expiresAtMs <= nowMs) {
    throw new OperatorError(
      'EXECUTION_MENTION_DELEGATION_EXPIRED',
      'TTL da mention delegation expirou antes da execução.',
      {
        messageId: mentionDelegation.messageId,
        expiresAt: mentionDelegation.expiresAt,
        now: new Date(nowMs).toISOString()
      }
    );
  }

  const dedupeKey = `${mentionDelegation.targetBotId}:${mentionDelegation.messageId}`;
  const lockFd = await acquireLock({ timeoutMs: lockTimeoutMs, staleMs: lockStaleMs });

  try {
    const store = readStore();
    pruneExpired(store, nowMs);

    const existing = store.entries[dedupeKey];
    const existingExpiresAtMs = Number(existing?.expiresAtMs ?? 0);
    if (existing && Number.isFinite(existingExpiresAtMs) && existingExpiresAtMs > nowMs) {
      throw new OperatorError(
        'EXECUTION_MENTION_DELEGATION_DUPLICATE',
        'messageId já consumido na janela de dedupe para mention delegation.',
        {
          dedupeKey,
          firstSeenAt: existing.firstSeenAt,
          expiresAt: existing.expiresAt
        }
      );
    }

    store.entries[dedupeKey] = {
      dedupeKey,
      messageId: mentionDelegation.messageId,
      channel: mentionDelegation.channel,
      originBotId: mentionDelegation.originBotId,
      targetBotId: mentionDelegation.targetBotId,
      observedAt: mentionDelegation.observedAt,
      ttlSeconds: mentionDelegation.ttlSeconds,
      firstSeenAt: new Date(nowMs).toISOString(),
      expiresAt: mentionDelegation.expiresAt,
      expiresAtMs
    };

    writeStore(store);
  } finally {
    releaseLock(lockFd);
  }

  return {
    dedupeKey,
    ttlSeconds: mentionDelegation.ttlSeconds,
    expiresAt: mentionDelegation.expiresAt
  };
}

export function getMentionDelegationStorePath() {
  return DEDUPE_STORE_PATH;
}

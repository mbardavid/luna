import fs from 'node:fs';
import crypto from 'node:crypto';
import test from 'node:test';
import assert from 'node:assert/strict';
import { runExecutionPayload } from '../src/core/executor.mjs';
import { getA2ANonceStorePath } from '../src/core/a2a-security.mjs';
import { fromRoot } from '../src/utils/paths.mjs';
import { stableStringify } from '../src/utils/fs.mjs';

const NONCE_LOCK_PATH = fromRoot('state', 'a2a-nonce.lock');

function withEnv(overrides, fn) {
  const snapshot = {};

  for (const [key, value] of Object.entries(overrides)) {
    snapshot[key] = process.env[key];
    if (value == null) {
      delete process.env[key];
    } else {
      process.env[key] = String(value);
    }
  }

  return Promise.resolve()
    .then(fn)
    .finally(() => {
      for (const [key, previous] of Object.entries(snapshot)) {
        if (previous == null) delete process.env[key];
        else process.env[key] = previous;
      }
    });
}

function signPayload(payload, secret) {
  const clone = JSON.parse(JSON.stringify(payload));
  delete clone.auth.signature;
  const digest = crypto.createHmac('sha256', secret).update(stableStringify(clone)).digest('hex');
  return {
    ...payload,
    auth: {
      ...payload.auth,
      signature: digest
    }
  };
}

function resetNonceState() {
  const storePath = getA2ANonceStorePath();
  if (fs.existsSync(storePath)) fs.unlinkSync(storePath);
  if (fs.existsSync(NONCE_LOCK_PATH)) fs.unlinkSync(NONCE_LOCK_PATH);
}

test('execution-plane live rejects unsigned payload when A2A enforce mode is active', async () => {
  await withEnv(
    {
      A2A_SECURITY_MODE: 'enforce',
      A2A_HMAC_KEYS_JSON: JSON.stringify({ 'bot-alpha': 'secret-1' }),
      A2A_ALLOW_UNSIGNED_LIVE: 'false'
    },
    async () => {
      const result = await runExecutionPayload({
        payload: {
          schemaVersion: 'v1',
          plane: 'execution',
          operation: 'transfer',
          requestId: 'req_2026-02-18_sec_001',
          correlationId: 'corr_2026-02-18_sec_001',
          intent: {
            chain: 'base',
            asset: 'ETH',
            amount: '0.001',
            to: '0x000000000000000000000000000000000000dEaD'
          }
        },
        dryRun: false,
        policyPath: fromRoot('config', 'policy.live.example.json')
      });

      assert.equal(result.ok, false);
      assert.equal(result.error.code, 'A2A_AUTH_REQUIRED');
    }
  );
});

test('execution-plane accepts valid signature and blocks replayed nonce', async () => {
  resetNonceState();

  await withEnv(
    {
      A2A_SECURITY_MODE: 'enforce',
      A2A_HMAC_KEYS_JSON: JSON.stringify({ 'bot-alpha': 'secret-1' }),
      A2A_ALLOW_UNSIGNED_LIVE: 'false'
    },
    async () => {
      const timestamp = new Date().toISOString();
      const basePayload = {
        schemaVersion: 'v1',
        plane: 'execution',
        operation: 'transfer',
        requestId: 'req_2026-02-18_sec_002',
        correlationId: 'corr_2026-02-18_sec_002',
        timestamp,
        auth: {
          scheme: 'hmac-sha256-v1',
          keyId: 'bot-alpha',
          nonce: 'nonce-security-test-001',
          timestamp,
          signature: ''
        },
        intent: {
          chain: 'base',
          asset: 'ETH',
          amount: '0.001',
          to: '0x000000000000000000000000000000000000dEaD'
        }
      };

      const signedPayload = signPayload(basePayload, 'secret-1');

      const first = await runExecutionPayload({
        payload: signedPayload,
        dryRun: true
      });

      assert.equal(first.ok, true);
      assert.equal(first.executionPlane.security.verified, true);
      assert.equal(first.executionPlane.security.keyId, 'bot-alpha');

      const replay = await runExecutionPayload({
        payload: signedPayload,
        dryRun: true
      });

      assert.equal(replay.ok, false);
      assert.equal(replay.error.code, 'A2A_NONCE_REPLAY');
    }
  );
});

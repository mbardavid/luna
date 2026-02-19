import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';
import { runExecutionPayload } from '../src/core/executor.mjs';
import { fromRoot } from '../src/utils/paths.mjs';

const POLICY_PATH = fromRoot('tests', 'fixtures', 'policy.all-ops.json');
const EXAMPLES_DIR = fromRoot('docs', 'examples', 'a2a-v1');

const FIXTURES = [
  'bridge-solana-hyperliquid.json',
  'bridge-base-hyperliquid.json',
  'hyperliquid-bridge-deposit.json',
  'hyperliquid-bridge-withdraw.json',
  'bridge-hyperliquid-base.json',
  'bridge-hyperliquid-solana.json'
];

for (const fileName of FIXTURES) {
  test(`hl multiroute fixture dry-run: ${fileName}`, async () => {
    const payload = JSON.parse(fs.readFileSync(path.join(EXAMPLES_DIR, fileName), 'utf8'));

    const result = await runExecutionPayload({
      payload,
      dryRun: true,
      policyPath: POLICY_PATH
    });

    const expectedError = payload?.meta?.expectedErrorCode ?? null;
    if (expectedError) {
      assert.equal(result.ok, false);
      assert.equal(result.error.code, expectedError);
      return;
    }

    assert.equal(result.ok, true);
    assert.ok(['hyperliquid_bridge', 'debridge'].includes(result.result.connector));
  });
}

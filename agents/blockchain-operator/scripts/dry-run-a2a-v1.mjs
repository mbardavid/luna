#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { runExecutionPayload } from '../src/core/executor.mjs';
import { fromRoot } from '../src/utils/paths.mjs';

const POLICY_PATH = fromRoot('tests', 'fixtures', 'policy.all-ops.json');
const EXAMPLES_DIR = fromRoot('docs', 'examples', 'a2a-v1');
const ARTIFACT_PATH = fromRoot('artifacts', 'dry-run-a2a-v1-results.json');

const PAYLOAD_FILES = [
  'bridge.json',
  'bridge-solana-hyperliquid.json',
  'bridge-base-hyperliquid.json',
  'bridge-hyperliquid-base.json',
  'bridge-hyperliquid-solana.json',
  'hyperliquid-bridge-deposit.json',
  'hyperliquid-bridge-withdraw.json',
  'swap-jupiter.json',
  'swap-raydium.json',
  'swap-pumpfun.json',
  'defi-deposit.json',
  'defi-withdraw.json',
  'hyperliquid-spot-order.json',
  'hyperliquid-perp-order.json',
  'transfer.json',
  'send.json',
  'hyperliquid-cancel.json',
  'hyperliquid-modify.json',
  'hyperliquid-deposit.json'
];

function makeJsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get(name) {
        if (String(name).toLowerCase() === 'content-type') return 'application/json';
        return null;
      }
    },
    async text() {
      return JSON.stringify(body);
    },
    async arrayBuffer() {
      return Buffer.from(JSON.stringify(body)).buffer;
    }
  };
}

function mockFetch(url, options = {}) {
  const target = String(url);

  if (target.includes('/quote') && target.includes('jup')) {
    return Promise.resolve(
      makeJsonResponse({
        inAmount: '25000000',
        outAmount: '500000000',
        priceImpactPct: '0.0012',
        routePlan: [{ percent: 100 }]
      })
    );
  }

  if (target.includes('/compute/swap-base-in') && target.includes('raydium')) {
    return Promise.resolve(
      makeJsonResponse({
        success: true,
        data: {
          inAmount: '30000000',
          outAmount: '600000000'
        }
      })
    );
  }

  if (target.includes('/coins/') && target.includes('pump.fun')) {
    return Promise.resolve(
      makeJsonResponse({
        mint: '11111111111111111111111111111111',
        symbol: 'SAGE',
        complete: false
      })
    );
  }

  if (target.includes('/dln/order/create-tx')) {
    return Promise.resolve(
      makeJsonResponse({
        tx: {
          to: '0x000000000000000000000000000000000000dEaD',
          data: '0x1234',
          value: '0'
        },
        estimation: {
          outAmount: '49900000'
        },
        orderId: 'dryrun-order-1'
      })
    );
  }

  if (target.endsWith('/info')) {
    const payload = JSON.parse(options.body);

    if (payload.type === 'allMids') {
      return Promise.resolve(
        makeJsonResponse({
          BTC: '50000',
          'PURR/USDC': '0.3'
        })
      );
    }

    if (payload.type === 'spotClearinghouseState') {
      return Promise.resolve(
        makeJsonResponse({
          balances: [{ coin: 'USDC', total: '100', hold: '0' }]
        })
      );
    }

    throw new Error(`Unexpected Hyperliquid /info payload: ${JSON.stringify(payload)}`);
  }

  throw new Error(`Unexpected fetch URL in dry-run suite: ${target}`);
}

async function main() {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = mockFetch;

  const results = [];

  try {
    for (const fileName of PAYLOAD_FILES) {
      const filePath = path.join(EXAMPLES_DIR, fileName);
      const raw = fs.readFileSync(filePath, 'utf8');
      const payload = JSON.parse(raw);

      const startedAt = Date.now();
      const execution = await runExecutionPayload({
        payload,
        dryRun: true,
        policyPath: POLICY_PATH
      });

      const latencyMs = Date.now() - startedAt;
      const expectedErrorCode = payload?.meta?.expectedErrorCode ?? null;
      const passed = expectedErrorCode
        ? execution.ok === false && execution.error?.code === expectedErrorCode
        : execution.ok === true;

      results.push({
        file: fileName,
        operation: payload.operation,
        passed,
        expectedErrorCode,
        ok: execution.ok,
        code: execution.ok ? null : execution.error.code,
        latencyMs,
        runId: execution.runId,
        connector: execution.ok ? execution.result.connector ?? null : null,
        planMode: execution.ok ? execution.plan.mode : null
      });
    }
  } finally {
    globalThis.fetch = originalFetch;
  }

  fs.mkdirSync(path.dirname(ARTIFACT_PATH), { recursive: true });
  fs.writeFileSync(
    ARTIFACT_PATH,
    `${JSON.stringify({
      generatedAt: new Date().toISOString(),
      policyPath: POLICY_PATH,
      results
    }, null, 2)}\n`,
    'utf8'
  );

  console.log(JSON.stringify({ ok: results.every((r) => r.passed), artifact: ARTIFACT_PATH, results }, null, 2));

  if (results.some((row) => !row.passed)) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(
    JSON.stringify(
      {
        ok: false,
        error: {
          message: error.message,
          stack: error.stack
        }
      },
      null,
      2
    )
  );
  process.exitCode = 1;
});

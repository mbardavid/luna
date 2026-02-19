import fs from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { runInstruction, runNativeCommand } from '../src/core/executor.mjs';
import { ConsolidatedBalanceService, formatConsolidatedBalanceForDiscord } from '../src/core/portfolio-balance.mjs';
import { NonceCoordinator, getNonceStorePath } from '../src/core/nonce-coordinator.mjs';
import { JupiterConnector } from '../src/connectors/jupiter.mjs';
import { RaydiumConnector } from '../src/connectors/raydium.mjs';
import { OperatorError } from '../src/utils/errors.mjs';
import { fromRoot } from '../src/utils/paths.mjs';

function makeInfoResponse(body) {
  return {
    ok: true,
    status: 200,
    async text() {
      return JSON.stringify(body);
    }
  };
}

function mockHyperliquidFetch({ mids = { BTC: '50000' }, openOrders = null, clearinghouseState = null, spotState = null } = {}) {
  return async (_url, options) => {
    const payload = JSON.parse(options.body);

    if (payload.type === 'allMids') {
      return makeInfoResponse(mids);
    }

    if (payload.type === 'spotMetaAndAssetCtxs') {
      return makeInfoResponse([
        {},
        [
          {
            coin: 'BTC',
            midPx: String(mids.BTC)
          }
        ]
      ]);
    }

    if (payload.type === 'frontendOpenOrders') {
      return makeInfoResponse(openOrders ?? []);
    }

    if (payload.type === 'clearinghouseState') {
      return makeInfoResponse(
        clearinghouseState ?? {
          crossMarginSummary: {
            accountValue: '1000',
            totalMarginUsed: '0'
          },
          marginSummary: {
            accountValue: '1000',
            totalMarginUsed: '0'
          },
          assetPositions: []
        }
      );
    }

    if (payload.type === 'spotClearinghouseState') {
      return makeInfoResponse(spotState ?? { balances: [] });
    }

    throw new Error(`Unexpected /info payload in test: ${JSON.stringify(payload)}`);
  };
}

function withPatchedFetch(fetchImpl, fn) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchImpl;
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      globalThis.fetch = originalFetch;
    });
}

test('pipeline parser->plan->execute dry-run for Hyperliquid order', async () => {
  const prevAccount = process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  const prevUser = process.env.HYPERLIQUID_USER_ADDRESS;
  delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  delete process.env.HYPERLIQUID_USER_ADDRESS;

  try {
    await withPatchedFetch(mockHyperliquidFetch({ mids: { BTC: '50000' } }), async () => {
      const result = await runInstruction({
        instruction: 'buy 0.001 BTC perp at market on hyperliquid',
        dryRun: true
      });

      assert.equal(result.ok, true);
      assert.equal(result.dryRun, true);
      assert.equal(result.intent.action, 'hl_order');
      assert.equal(result.plan.mode, 'dry-run');
      assert.equal(result.result.preflight.referencePrice, 50000);
      assert.equal(result.result.preflight.notionalUsd, 50);
      assert.equal(result.result.preflight.walletReady, false);
    });
  } finally {
    if (prevAccount == null) delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
    else process.env.HYPERLIQUID_ACCOUNT_ADDRESS = prevAccount;

    if (prevUser == null) delete process.env.HYPERLIQUID_USER_ADDRESS;
    else process.env.HYPERLIQUID_USER_ADDRESS = prevUser;
  }
});

test('swap_jupiter falls back to raydium when jupiter preflight has network failure', async () => {
  const originalJupiterPreflight = JupiterConnector.prototype.preflightSwap;
  const originalRaydiumPreflight = RaydiumConnector.prototype.preflightSwap;

  JupiterConnector.prototype.preflightSwap = async () => {
    throw new OperatorError('JUPITER_PREFLIGHT_FAILED', 'Falha no preflight do swap Jupiter', {
      message: 'fetch failed'
    });
  };

  RaydiumConnector.prototype.preflightSwap = async (intent) => ({
    connector: 'raydium',
    action: 'swap_raydium',
    requestedAmount: intent.amount,
    inAsset: intent.assetIn,
    outAsset: intent.assetOut,
    walletReady: false
  });

  try {
    const result = await runInstruction({
      instruction: 'troque 1 SOL por USDC',
      dryRun: true
    });

    assert.equal(result.ok, true);
    assert.equal(result.intent.action, 'swap_jupiter');
    assert.equal(result.result.connector, 'raydium');
    assert.equal(result.result.fallback.from, 'jupiter');
    assert.equal(result.result.preflight.action, 'swap_raydium');
  } finally {
    JupiterConnector.prototype.preflightSwap = originalJupiterPreflight;
    RaydiumConnector.prototype.preflightSwap = originalRaydiumPreflight;
  }
});

test('native /saldo command uses unified portfolio_balance valuation pipeline', async () => {
  const originalGetSnapshot = ConsolidatedBalanceService.prototype.getSnapshot;

  const snapshot = {
    snapshotUtc: '2026-02-18T04:38:12.183Z',
    wallets: [
      {
        network: 'base',
        walletAddress: '0xa1464EB4f86958823b0f24B3CF5Ac2b8134D6bb1',
        rows: [],
        errors: [],
        warnings: [],
        meta: {},
        subtotalUsd: 0,
        subtotalHint: null
      },
      {
        network: 'solana',
        walletAddress: 'BYzZUKToZbkfe1snHFHSsTKMTb1SNmyCf8tGYTUgnbr',
        rows: [
          {
            asset: 'SOL',
            quantity: 5.146141608,
            valuationKind: 'spot',
            priceSymbol: 'SOL',
            entryPriceUsd: null,
            bucket: 'spot',
            fixedPriceUsd: null,
            fixedValueUsd: null,
            notes: [],
            priceUsd: 85.149,
            priceSource: 'pyth',
            priceUpdatedAt: '2026-02-18T04:38:16.000Z',
            valueUsd: 438.18881177959196
          }
        ],
        errors: [],
        warnings: [],
        meta: {},
        subtotalUsd: 438.18881177959196,
        subtotalHint: null
      },
      {
        network: 'hyperliquid',
        walletAddress: '0x1113B4e00397997EBdaaC95ceb90cf97bD4D51dd',
        rows: [
          {
            asset: 'USD-COLLATERAL',
            quantity: 0,
            valuationKind: 'fixed',
            priceSymbol: 'USD-COLLATERAL',
            entryPriceUsd: null,
            bucket: 'perp',
            fixedPriceUsd: 1,
            fixedValueUsd: 0,
            notes: [],
            priceUsd: 1,
            priceSource: 'fixed',
            priceUpdatedAt: null,
            valueUsd: 0
          }
        ],
        errors: [],
        warnings: [],
        meta: {
          perpAccountValueUsd: 0
        },
        subtotalUsd: 0,
        subtotalHint: 'spot mark-to-market + perp equity'
      }
    ],
    totalUsd: 438.18881177959196,
    unpricedAssets: [],
    partialFailures: [],
    marketData: {
      primary: 'chainlink',
      fallback: 'pyth'
    }
  };

  const mockedSnapshot = {
    ...snapshot,
    discordMessage: formatConsolidatedBalanceForDiscord(snapshot),
    generatedAtUtc: '2026-02-18T04:38:17.516Z'
  };

  ConsolidatedBalanceService.prototype.getSnapshot = async () => mockedSnapshot;

  try {
    const result = await runNativeCommand({
      command: '/saldo',
      dryRun: true
    });

    assert.equal(result.ok, true);
    assert.equal(result.source, 'native_command');
    assert.equal(result.intent.action, 'portfolio_balance');
    assert.equal(result.nativeCommand.action, 'portfolio_balance');
    assert.equal(result.nativeCommand.resolvedInstruction, '/saldo');
    assert.equal(result.result.connector, 'portfolio');
    assert.match(result.result.discordMessage, /token\s+\|\s+wallet\s+\|\s+qty\s+\|\s+price_usd\s+\|\s+value_usd\s+\|\s+%/);
    assert.match(result.result.discordMessage, /SOL\s+\|\s+Solana:BYzZUK\.\.\.gnbr\s+\|\s+5\.146142\s+\|\s+85\.149000\s+\|\s+438\.19\s+\|\s+100\.00%/);
    assert.match(result.result.discordMessage, /Total USD: \$438\.19/);
    assert.equal(result.plan.steps.some((step) => step.id === 'mark-to-market'), true);
    assert.equal(result.plan.steps.some((step) => step.id === 'format-discord-response'), true);
  } finally {
    ConsolidatedBalanceService.prototype.getSnapshot = originalGetSnapshot;
  }
});

test('native saldo and /saldo return the same portfolio format', async () => {
  const originalGetSnapshot = ConsolidatedBalanceService.prototype.getSnapshot;

  const snapshot = {
    snapshotUtc: '2026-02-18T05:10:00.000Z',
    wallets: [
      {
        network: 'base',
        walletAddress: '0x000000000000000000000000000000000000dEaD',
        rows: [
          {
            asset: 'ETH',
            quantity: 1,
            valuationKind: 'spot',
            priceSymbol: 'ETH',
            entryPriceUsd: null,
            bucket: 'spot',
            fixedPriceUsd: null,
            fixedValueUsd: null,
            notes: [],
            priceUsd: 2000,
            priceSource: 'chainlink',
            priceUpdatedAt: '2026-02-18T05:09:00.000Z',
            valueUsd: 2000
          }
        ],
        errors: [],
        warnings: [],
        meta: {},
        subtotalUsd: 2000,
        subtotalHint: null
      },
      {
        network: 'solana',
        walletAddress: '11111111111111111111111111111111',
        rows: [],
        errors: [],
        warnings: [],
        meta: {},
        subtotalUsd: 0,
        subtotalHint: null
      },
      {
        network: 'hyperliquid',
        walletAddress: '0x1111111111111111111111111111111111111111',
        rows: [],
        errors: [],
        warnings: [],
        meta: { perpAccountValueUsd: 0 },
        subtotalUsd: 0,
        subtotalHint: 'spot mark-to-market + perp equity'
      }
    ],
    totalUsd: 2000,
    unpricedAssets: [],
    partialFailures: [],
    marketData: {
      primary: 'chainlink',
      fallback: 'pyth'
    }
  };

  const mockedSnapshot = {
    ...snapshot,
    discordMessage: formatConsolidatedBalanceForDiscord(snapshot),
    generatedAtUtc: '2026-02-18T05:10:02.000Z'
  };

  ConsolidatedBalanceService.prototype.getSnapshot = async () => mockedSnapshot;

  try {
    const slash = await runNativeCommand({ command: '/saldo', dryRun: true });
    const alias = await runNativeCommand({ command: 'saldo', dryRun: true });

    assert.equal(slash.ok, true);
    assert.equal(alias.ok, true);
    assert.equal(alias.result.discordMessage, slash.result.discordMessage);
  } finally {
    ConsolidatedBalanceService.prototype.getSnapshot = originalGetSnapshot;
  }
});

test('dry-run cancel validates open order presence', async () => {
  const prevAccount = process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  process.env.HYPERLIQUID_ACCOUNT_ADDRESS = '0x1111111111111111111111111111111111111111';

  try {
    await withPatchedFetch(
      mockHyperliquidFetch({
        openOrders: [
          {
            coin: 'BTC',
            oid: 123,
            cloid: null,
            side: 'B',
            limitPx: '50000',
            sz: '0.001',
            reduceOnly: false
          }
        ]
      }),
      async () => {
        const result = await runInstruction({
          instruction: 'cancel order 123 BTC perp on hyperliquid',
          dryRun: true
        });

        assert.equal(result.ok, true);
        assert.equal(result.intent.action, 'hl_cancel');
        assert.equal(result.result.preflight.found.oid, 123);
      }
    );
  } finally {
    if (prevAccount == null) delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
    else process.env.HYPERLIQUID_ACCOUNT_ADDRESS = prevAccount;
  }
});

test('dry-run modify executes parser->plan->preflight for current+new order', async () => {
  const prevAccount = process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  process.env.HYPERLIQUID_ACCOUNT_ADDRESS = '0x1111111111111111111111111111111111111111';

  try {
    await withPatchedFetch(
      mockHyperliquidFetch({
        mids: { BTC: '50000' },
        openOrders: [
          {
            coin: 'BTC',
            oid: 123,
            cloid: null,
            side: 'B',
            limitPx: '50000',
            sz: '0.001',
            reduceOnly: false
          }
        ],
        clearinghouseState: {
          crossMarginSummary: {
            accountValue: '1000',
            totalMarginUsed: '100'
          },
          marginSummary: {
            accountValue: '1000',
            totalMarginUsed: '100'
          },
          assetPositions: []
        }
      }),
      async () => {
        const result = await runInstruction({
          instruction: 'modify order 123 buy 0.001 BTC perp at 45000 on hyperliquid',
          dryRun: true
        });

        assert.equal(result.ok, true);
        assert.equal(result.intent.action, 'hl_modify');
        assert.equal(result.plan.steps.some((s) => s.id === 'preflight-hl-modify'), true);
        assert.equal(result.result.preflight.currentOrder.oid, 123);
        assert.equal(result.result.preflight.nextOrder.notionalUsd, 45);
      }
    );
  } finally {
    if (prevAccount == null) delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
    else process.env.HYPERLIQUID_ACCOUNT_ADDRESS = prevAccount;
  }
});

test('policy guardrails apply default slippage + notional for HL market order', async () => {
  const prevAccount = process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  const prevUser = process.env.HYPERLIQUID_USER_ADDRESS;
  delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  delete process.env.HYPERLIQUID_USER_ADDRESS;

  const policyPath = fromRoot('tests', 'fixtures', 'policy.guardrails.json');

  try {
    await withPatchedFetch(mockHyperliquidFetch({ mids: { BTC: '50000' } }), async () => {
      const pass = await runInstruction({
        instruction: 'buy 0.001 BTC perp at market on hyperliquid',
        dryRun: true,
        policyPath
      });

      assert.equal(pass.ok, true);
      assert.equal(pass.intent.slippageBps, '50');
      assert.equal(pass.intent.referencePrice, '50000');

      const fail = await runInstruction({
        instruction: 'buy 0.01 BTC perp at market on hyperliquid',
        dryRun: true,
        policyPath
      });

      assert.equal(fail.ok, false);
      assert.equal(fail.error.code, 'POLICY_NOTIONAL_EXCEEDED');
    });
  } finally {
    if (prevAccount == null) delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
    else process.env.HYPERLIQUID_ACCOUNT_ADDRESS = prevAccount;

    if (prevUser == null) delete process.env.HYPERLIQUID_USER_ADDRESS;
    else process.env.HYPERLIQUID_USER_ADDRESS = prevUser;
  }
});

test('policy guardrail maxOrderSize blocks oversized HL orders', async () => {
  const policyPath = fromRoot('tests', 'fixtures', 'policy.guardrails.json');

  const result = await runInstruction({
    instruction: 'buy 3 BTC perp at 1 on hyperliquid',
    dryRun: true,
    policyPath
  });

  assert.equal(result.ok, false);
  assert.equal(result.error.code, 'POLICY_ORDER_SIZE_EXCEEDED');
});

test('idempotency key stays stable with policy hydration market refs', async () => {
  const prevAccount = process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  const prevUser = process.env.HYPERLIQUID_USER_ADDRESS;
  delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  delete process.env.HYPERLIQUID_USER_ADDRESS;

  const policyPath = fromRoot('tests', 'fixtures', 'policy.guardrails.json');

  try {
    const first = await withPatchedFetch(mockHyperliquidFetch({ mids: { BTC: '50000' } }), async () =>
      runInstruction({
        instruction: 'buy 0.001 BTC perp at market on hyperliquid',
        dryRun: true,
        policyPath
      })
    );

    const second = await withPatchedFetch(mockHyperliquidFetch({ mids: { BTC: '51000' } }), async () =>
      runInstruction({
        instruction: 'buy 0.001 BTC perp at market on hyperliquid',
        dryRun: true,
        policyPath
      })
    );

    assert.equal(first.ok, true);
    assert.equal(second.ok, true);
    assert.equal(first.idempotencyKey, second.idempotencyKey);
    assert.notEqual(first.intent.referencePrice, second.intent.referencePrice);
  } finally {
    if (prevAccount == null) delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
    else process.env.HYPERLIQUID_ACCOUNT_ADDRESS = prevAccount;

    if (prevUser == null) delete process.env.HYPERLIQUID_USER_ADDRESS;
    else process.env.HYPERLIQUID_USER_ADDRESS = prevUser;
  }
});

test('live execution enforces mandatory key segregation configuration', async () => {
  const snapshot = {
    base: process.env.BASE_PRIVATE_KEY,
    solB58: process.env.SOLANA_PRIVATE_KEY_B58,
    solJson: process.env.SOLANA_PRIVATE_KEY_JSON,
    hl: process.env.HYPERLIQUID_API_WALLET_PRIVATE_KEY
  };

  delete process.env.BASE_PRIVATE_KEY;
  delete process.env.SOLANA_PRIVATE_KEY_B58;
  delete process.env.SOLANA_PRIVATE_KEY_JSON;
  delete process.env.HYPERLIQUID_API_WALLET_PRIVATE_KEY;

  try {
    const result = await runInstruction({
      instruction: 'send 0.001 ETH to 0x000000000000000000000000000000000000dEaD on base',
      policyPath: fromRoot('config', 'policy.live.example.json')
    });

    assert.equal(result.ok, false);
    assert.equal(result.error.code, 'KEY_SEGREGATION_KEYS_MISSING');
  } finally {
    if (snapshot.base == null) delete process.env.BASE_PRIVATE_KEY;
    else process.env.BASE_PRIVATE_KEY = snapshot.base;

    if (snapshot.solB58 == null) delete process.env.SOLANA_PRIVATE_KEY_B58;
    else process.env.SOLANA_PRIVATE_KEY_B58 = snapshot.solB58;

    if (snapshot.solJson == null) delete process.env.SOLANA_PRIVATE_KEY_JSON;
    else process.env.SOLANA_PRIVATE_KEY_JSON = snapshot.solJson;

    if (snapshot.hl == null) delete process.env.HYPERLIQUID_API_WALLET_PRIVATE_KEY;
    else process.env.HYPERLIQUID_API_WALLET_PRIVATE_KEY = snapshot.hl;
  }
});

test('bridge requires explicit whitelisted recipient when withdrawals are restricted', async () => {
  const result = await runInstruction({
    instruction: 'bridge 1 USDC from base to solana',
    dryRun: true,
    policyPath: fromRoot('config', 'policy.live.example.json')
  });

  assert.equal(result.ok, false);
  assert.equal(result.error.code, 'POLICY_BRIDGE_RECIPIENT_REQUIRED');
});

test('nonce coordinator emits unique monotonic nonces under concurrency', async () => {
  const nonceStore = getNonceStorePath();
  const lockPath = fromRoot('state', 'hyperliquid-nonce.lock');

  if (fs.existsSync(nonceStore)) fs.unlinkSync(nonceStore);
  if (fs.existsSync(lockPath)) fs.unlinkSync(lockPath);

  const coordinator = new NonceCoordinator({ timeoutMs: 3000, staleMs: 2000 });
  const signer = '0x1111111111111111111111111111111111111111';

  const values = await Promise.all(Array.from({ length: 20 }, () => coordinator.nextNonce({ signer })));

  const unique = new Set(values);
  assert.equal(unique.size, values.length);

  const sorted = [...values].sort((a, b) => a - b);
  for (let i = 1; i < sorted.length; i += 1) {
    assert.ok(sorted[i] > sorted[i - 1]);
  }
});

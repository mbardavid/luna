import fs from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseExecutionPayload } from '../src/core/execution-plane.mjs';
import { runExecutionPayload } from '../src/core/executor.mjs';
import { getMentionDelegationStorePath } from '../src/core/mention-delegation-gate.mjs';
import { fromRoot } from '../src/utils/paths.mjs';

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

function mockFetchRouter() {
  return async (url, options = {}) => {
    const target = String(url);

    if (target.includes('/quote') && target.includes('jup')) {
      return makeJsonResponse({
        inAmount: '5000000',
        outAmount: '25000000',
        priceImpactPct: '0.001',
        routePlan: [{ percent: 100 }]
      });
    }

    if (target.includes('/compute/swap-base-in') && target.includes('raydium')) {
      return makeJsonResponse({
        success: true,
        data: {
          inAmount: '7000000',
          outAmount: '2000000',
          routePlan: [{ poolId: 'pool-1' }]
        }
      });
    }

    if (target.includes('/coins/') && target.includes('pump.fun')) {
      return makeJsonResponse({
        mint: 'HSGfLrCiMpnGW8o7qhad79JU6aZH1TW37tpsqdRhuUPx',
        symbol: 'SAGE',
        complete: false
      });
    }

    if (target.includes('/dln/order/create-tx')) {
      return makeJsonResponse({
        tx: {
          to: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6',
          data: '0x1234',
          value: '0'
        },
        estimation: {
          srcChainTokenIn: 'USDC',
          dstChainTokenOut: 'USDC'
        },
        orderId: 'order-test-1'
      });
    }

    if (target.endsWith('/info')) {
      const payload = JSON.parse(options.body);
      if (payload.type === 'allMids') {
        return makeJsonResponse({ BTC: '50000' });
      }

      if (payload.type === 'spotClearinghouseState') {
        return makeJsonResponse({ balances: [{ coin: 'USDC', total: '25', hold: '0' }] });
      }

      throw new Error(`Unexpected Hyperliquid payload in test: ${JSON.stringify(payload)}`);
    }

    throw new Error(`Unexpected fetch URL in test: ${target}`);
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

function makeMentionDelegationMeta({ messageId, targetBotId = 'blockchain-operator', riskClassification = 'read' } = {}) {
  const finalMessageId = messageId ?? String(Date.now()).padEnd(18, '0');

  return {
    mentionDelegationMode: 'gated',
    mentionDelegation: {
      channel: 'discord:channel:1473392629055098942',
      messageId: finalMessageId,
      originBotId: 'decision-router',
      targetBotId,
      observedAt: '2099-02-18T19:36:58Z',
      ttlSeconds: 300,
      dedupeBy: 'messageId',
      delegatedHumanProxy: {
        mode: 'delegated-human-proxy',
        policyValidated: true,
        envelopeValidated: true,
        riskGatePassed: true,
        riskClassification,
        authorizationRef:
          riskClassification === 'sensitive' || riskClassification === 'live' ? 'authz_test_mention_001' : null
      }
    }
  };
}

function removeMentionDedupeStore() {
  try {
    fs.unlinkSync(getMentionDelegationStorePath());
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}

test('execution payload schema validates + normalizes transfer intent', () => {
  const payload = {
    schemaVersion: 'v1',
    plane: 'execution',
    operation: 'transfer',
    requestId: 'req_2026-02-18_0001',
    correlationId: 'decisionbot-cycle-77',
    idempotencyKey: 'transfer-abc-123',
    intent: {
      chain: 'base',
      asset: 'ETH',
      amount: '1.25',
      to: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6'
    }
  };

  const parsed = parseExecutionPayload(payload);

  assert.equal(parsed.envelope.operation, 'transfer');
  assert.equal(parsed.canonicalIntent.action, 'transfer');
  assert.equal(parsed.canonicalIntent.chain, 'base');
  assert.equal(parsed.canonicalIntent.asset, 'ETH');
  assert.equal(parsed.canonicalIntent.amount, '1.25');
  assert.equal(parsed.canonicalIntent.raw, null);
});

test('execution payload schema rejects invalid correlationId format', () => {
  assert.throws(
    () =>
      parseExecutionPayload({
        schemaVersion: 'v1',
        plane: 'execution',
        operation: 'transfer',
        requestId: 'req_2026-02-18_0002',
        correlationId: 'x',
        intent: {
          chain: 'base',
          asset: 'ETH',
          amount: '1',
          to: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6'
        }
      }),
    (error) => error?.code === 'EXECUTION_SCHEMA_INVALID'
  );
});

test('execution payload schema accepts mention delegation gated metadata', () => {
  const payload = {
    schemaVersion: 'v1',
    plane: 'execution',
    operation: 'transfer',
    requestId: 'req_2026-02-18_0012',
    correlationId: 'decisionbot-cycle-87',
    intent: {
      chain: 'base',
      asset: 'ETH',
      amount: '0.01',
      to: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6'
    },
    meta: makeMentionDelegationMeta({ messageId: '1473395000000000999' })
  };

  const parsed = parseExecutionPayload(payload);

  assert.equal(parsed.envelope.mentionDelegation.mode, 'gated');
  assert.equal(parsed.envelope.mentionDelegation.messageId, '1473395000000000999');
  assert.equal(parsed.envelope.mentionDelegation.dedupeBy, 'messageId');
});

test('execution payload schema rejects mention delegation loop (origin == target)', () => {
  const payload = {
    schemaVersion: 'v1',
    plane: 'execution',
    operation: 'transfer',
    requestId: 'req_2026-02-18_0013',
    correlationId: 'decisionbot-cycle-88',
    intent: {
      chain: 'base',
      asset: 'ETH',
      amount: '0.01',
      to: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6'
    },
    meta: makeMentionDelegationMeta({
      messageId: '1473395000000000888',
      targetBotId: 'decision-router'
    })
  };

  assert.throws(() => parseExecutionPayload(payload), (error) => error?.code === 'EXECUTION_MENTION_DELEGATION_LOOP');
});

test('execution payload schema rejects mentionDelegation payload with disabled mode', () => {
  const meta = makeMentionDelegationMeta({ messageId: '1473395000000000666' });
  meta.mentionDelegationMode = 'disabled';

  const payload = {
    schemaVersion: 'v1',
    plane: 'execution',
    operation: 'transfer',
    requestId: 'req_2026-02-18_0015',
    correlationId: 'decisionbot-cycle-90',
    intent: {
      chain: 'base',
      asset: 'ETH',
      amount: '0.01',
      to: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6'
    },
    meta
  };

  assert.throws(() => parseExecutionPayload(payload), (error) => error?.code === 'EXECUTION_MENTION_DELEGATION_INVALID');
});

test('execution plane mention delegation dedupes by messageId within TTL', async () => {
  removeMentionDedupeStore();

  const payload = {
    schemaVersion: 'v1',
    plane: 'execution',
    operation: 'transfer',
    requestId: 'req_2026-02-18_0014',
    correlationId: 'decisionbot-cycle-89',
    intent: {
      chain: 'base',
      asset: 'ETH',
      amount: '0.01',
      to: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6'
    },
    meta: makeMentionDelegationMeta({ messageId: '1473395000000000777' })
  };

  const first = await runExecutionPayload({ payload, dryRun: true });
  const second = await runExecutionPayload({ payload, dryRun: true });

  assert.equal(first.ok, true);
  assert.equal(second.ok, false);
  assert.equal(second.error.code, 'EXECUTION_MENTION_DELEGATION_DUPLICATE');
});

test('execution plane dry-run transfer pipeline succeeds', async () => {
  const result = await runExecutionPayload({
    payload: {
      schemaVersion: 'v1',
      plane: 'execution',
      operation: 'transfer',
      requestId: 'req_2026-02-18_0003',
      correlationId: 'decisionbot-cycle-78',
      intent: {
        chain: 'base',
        asset: 'ETH',
        amount: '0.001',
        to: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6'
      }
    },
    dryRun: true
  });

  assert.equal(result.ok, true);
  assert.equal(result.source, 'execution_plane');
  assert.equal(result.intent.action, 'transfer');
  assert.equal(result.plan.mode, 'dry-run');
  assert.equal(result.result.preflight.walletReady, false);
});

test('execution plane send preserves recipient policy guardrails', async () => {
  const result = await runExecutionPayload({
    payload: {
      schemaVersion: 'v1',
      plane: 'execution',
      operation: 'send',
      requestId: 'req_2026-02-18_0005',
      correlationId: 'decisionbot-cycle-80',
      intent: {
        chain: 'base',
        asset: 'ETH',
        amount: '0.001',
        to: '0x000000000000000000000000000000000000bEEF',
        purpose: 'payout-test'
      }
    },
    dryRun: true,
    policyPath: fromRoot('config', 'policy.live.example.json')
  });

  assert.equal(result.ok, false);
  assert.equal(result.error.code, 'POLICY_RECIPIENT_DENIED');
});

test('execution plane supports Jupiter swap dry-run with live connector path', async () => {
  await withPatchedFetch(mockFetchRouter(), async () => {
    const result = await runExecutionPayload({
      payload: {
        schemaVersion: 'v1',
        plane: 'execution',
        operation: 'swap.jupiter',
        requestId: 'req_2026-02-18_0006',
        correlationId: 'decisionbot-cycle-81',
        intent: {
          chain: 'solana',
          inAsset: 'USDC',
          outAsset: 'SOL',
          amount: '5',
          slippageBps: 75
        }
      },
      dryRun: true
    });

    assert.equal(result.ok, true);
    assert.equal(result.intent.action, 'swap_jupiter');
    assert.equal(result.result.connector, 'jupiter');
    assert.equal(result.result.preflight.requestedAmount, '5');
  });
});

test('execution plane supports Raydium swap dry-run', async () => {
  await withPatchedFetch(mockFetchRouter(), async () => {
    const result = await runExecutionPayload({
      payload: {
        schemaVersion: 'v1',
        plane: 'execution',
        operation: 'swap.raydium',
        requestId: 'req_2026-02-18_0007',
        correlationId: 'decisionbot-cycle-82',
        intent: {
          chain: 'solana',
          inAsset: 'USDC',
          outAsset: 'SOL',
          amount: '7',
          slippageBps: 90
        }
      },
      dryRun: true
    });

    assert.equal(result.ok, true);
    assert.equal(result.intent.action, 'swap_raydium');
    assert.equal(result.result.connector, 'raydium');
  });
});

test('execution plane supports Pump.fun trade dry-run', async () => {
  await withPatchedFetch(mockFetchRouter(), async () => {
    const result = await runExecutionPayload({
      payload: {
        schemaVersion: 'v1',
        plane: 'execution',
        operation: 'swap.pumpfun',
        requestId: 'req_2026-02-18_0008',
        correlationId: 'decisionbot-cycle-83',
        intent: {
          chain: 'solana',
          side: 'buy',
          symbol: 'SAGE',
          mint: 'HSGfLrCiMpnGW8o7qhad79JU6aZH1TW37tpsqdRhuUPx',
          amount: '1',
          amountType: 'quote',
          slippageBps: 250
        }
      },
      dryRun: true,
      policyPath: fromRoot('tests', 'fixtures', 'policy.pumpfun.json')
    });

    assert.equal(result.ok, true);
    assert.equal(result.intent.action, 'swap_pumpfun');
    assert.equal(result.result.connector, 'pumpfun');
    assert.equal(result.result.preflight.symbol, 'SAGE');
  });
});

test('execution plane supports DeFi deposit dry-run via adapter pattern', async () => {
  const result = await runExecutionPayload({
    payload: {
      schemaVersion: 'v1',
      plane: 'execution',
      operation: 'defi.deposit',
      requestId: 'req_2026-02-18_0009',
      correlationId: 'decisionbot-cycle-84',
      intent: {
        chain: 'base',
        protocol: 'aave-v3',
        target: 'USDC-main-pool',
        asset: 'USDC',
        amount: '10',
        minSharesOut: '9.5'
      }
    },
    dryRun: true
  });

  assert.equal(result.ok, true);
  assert.equal(result.intent.action, 'defi_deposit');
  assert.equal(result.result.connector, 'defi');
  assert.equal(result.result.preflight.protocol, 'aave-v3');
});

test('execution plane supports deBridge preflight dry-run', async () => {
  await withPatchedFetch(mockFetchRouter(), async () => {
    const result = await runExecutionPayload({
      payload: {
        schemaVersion: 'v1',
        plane: 'execution',
        operation: 'bridge',
        requestId: 'req_2026-02-18_0010',
        correlationId: 'decisionbot-cycle-85',
        intent: {
          fromChain: 'base',
          toChain: 'solana',
          asset: 'USDC',
          amount: '50',
          recipient: 'HSGfLrCiMpnGW8o7qhad79JU6aZH1TW37tpsqdRhuUPx'
        }
      },
      dryRun: true
    });

    assert.equal(result.ok, true);
    assert.equal(result.intent.action, 'bridge');
    assert.equal(result.result.connector, 'debridge');
    assert.equal(result.result.preflight.hasTxPayload, true);
  });
});

test('execution plane supports Hyperliquid perp order dry-run with structured payload', async () => {
  await withPatchedFetch(mockFetchRouter(), async () => {
    const result = await runExecutionPayload({
      payload: {
        schemaVersion: 'v1',
        plane: 'execution',
        operation: 'hyperliquid.perp.order',
        requestId: 'req_2026-02-18_0011',
        correlationId: 'decisionbot-cycle-86',
        intent: {
          market: 'BTC',
          side: 'buy',
          amount: '0.001',
          price: 'market',
          slippageBps: 50
        }
      },
      dryRun: true
    });

    assert.equal(result.ok, true);
    assert.equal(result.intent.action, 'hl_order');
    assert.equal(result.intent.venue, 'perp');
    assert.equal(result.result.preflight.referencePrice, 50000);
  });
});

test('execution plane supports Hyperliquid USDC deposit dry-run', async () => {
  const prevAccount = process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  process.env.HYPERLIQUID_ACCOUNT_ADDRESS = '0x1113b4e00397997ebdaac95ceb90cf97bd4d51dd';

  try {
    await withPatchedFetch(mockFetchRouter(), async () => {
      const result = await runExecutionPayload({
        payload: {
          schemaVersion: 'v1',
          plane: 'execution',
          operation: 'hyperliquid.deposit',
          requestId: 'req_2026-02-18_0016',
          correlationId: 'decisionbot-cycle-91',
          intent: {
            asset: 'USDC',
            amount: '1',
            toPerp: true
          }
        },
        dryRun: true
      });

      assert.equal(result.ok, true);
      assert.equal(result.intent.action, 'hl_deposit');
      assert.equal(result.result.connector, 'hyperliquid');
      assert.equal(result.result.preflight.checks.freeUsdc, 25);
    });
  } finally {
    if (prevAccount == null) delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
    else process.env.HYPERLIQUID_ACCOUNT_ADDRESS = prevAccount;
  }
});

test('execution plane supports Hyperliquid native bridge deposit dry-run', async () => {
  const result = await runExecutionPayload({
    payload: {
      schemaVersion: 'v1',
      plane: 'execution',
      operation: 'hyperliquid.bridge.deposit',
      requestId: 'req_2026-02-18_0017',
      correlationId: 'decisionbot-cycle-92',
      intent: {
        fromChain: 'arbitrum',
        toChain: 'hyperliquid',
        asset: 'USDC',
        amount: '10'
      }
    },
    dryRun: true
  });

  assert.equal(result.ok, true);
  assert.equal(result.intent.action, 'hl_bridge_deposit');
  assert.equal(result.result.connector, 'hyperliquid_bridge');
  assert.equal(result.result.preflight.bridgeAddress.toLowerCase(), '0x2df1c51e09aecf9cacb7bc98cb1742757f163df7');
});

test('execution plane supports Hyperliquid native bridge withdraw dry-run', async () => {
  const prevAccount = process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
  delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;

  try {
    const result = await runExecutionPayload({
      payload: {
        schemaVersion: 'v1',
        plane: 'execution',
        operation: 'hyperliquid.bridge.withdraw',
        requestId: 'req_2026-02-18_0018',
        correlationId: 'decisionbot-cycle-93',
        intent: {
          fromChain: 'hyperliquid',
          toChain: 'arbitrum',
          asset: 'USDC',
          amount: '6',
          recipient: '0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6'
        }
      },
      dryRun: true
    });

    assert.equal(result.ok, true);
    assert.equal(result.intent.action, 'hl_bridge_withdraw');
    assert.equal(result.result.connector, 'hyperliquid_bridge');
    assert.equal(result.result.preflight.hyperliquid.walletReady, false);
  } finally {
    if (prevAccount == null) delete process.env.HYPERLIQUID_ACCOUNT_ADDRESS;
    else process.env.HYPERLIQUID_ACCOUNT_ADDRESS = prevAccount;
  }
});

test('execution plane returns explicit NOT_SUPPORTED for deBridge route touching Hyperliquid', async () => {
  const result = await runExecutionPayload({
    payload: {
      schemaVersion: 'v1',
      plane: 'execution',
      operation: 'bridge',
      requestId: 'req_2026-02-18_0019',
      correlationId: 'decisionbot-cycle-94',
      intent: {
        fromChain: 'base',
        toChain: 'hyperliquid',
        asset: 'USDC',
        amount: '5',
        recipient: '0x1113b4e00397997ebdaac95ceb90cf97bd4d51dd'
      }
    },
    dryRun: true
  });

  assert.equal(result.ok, false);
  assert.equal(result.error.code, 'DEBRIDGE_HYPERLIQUID_ROUTE_NOT_SUPPORTED');
});
